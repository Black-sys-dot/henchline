package io.github.ninjatuna007.henchline

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.graphics.RectF
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.widget.ImageView

/** A picture to look into, as a messaging app's viewer (the user's wish, 2026-11-16): it opens fitted to the
 *  screen; pinch to zoom (up to six times), drag around while zoomed, double tap to zoom in there or back out.
 *  A single tap is the screen's (it shows or hides the bar); not zoomed, its pager takes sideways and downward drags. */
class ZoomImage(context: Context) : ImageView(context) {
    private val shown = Matrix()
    private var zoom = 1f  // over the fitted size
    var onTap: (() -> Unit)? = null
    val zoomed get() = zoom > 1.01f

    init {
        scaleType = ScaleType.MATRIX
    }

    override fun setImageBitmap(bitmap: Bitmap?) {
        super.setImageBitmap(bitmap)
        post { fit() }
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        fit()
    }

    /** Back to the whole picture: when it is swiped away. */
    fun reset() = fit()

    /** The whole picture, centred: where it opens, and where a double tap goes back to. */
    private fun fit() {
        val picture = drawable ?: return
        if (width == 0 || height == 0 || picture.intrinsicWidth <= 0 || picture.intrinsicHeight <= 0) return
        val fitted = minOf(width.toFloat() / picture.intrinsicWidth, height.toFloat() / picture.intrinsicHeight)
        shown.reset()
        shown.postScale(fitted, fitted)
        shown.postTranslate((width - picture.intrinsicWidth * fitted) / 2, (height - picture.intrinsicHeight * fitted) / 2)
        zoom = 1f
        imageMatrix = shown
    }

    private fun zoomBy(factor: Float, x: Float, y: Float) {
        val to = (zoom * factor).coerceIn(1f, 6f)
        val by = to / zoom
        zoom = to
        shown.postScale(by, by, x, y)
        keepInView()
        imageMatrix = shown
    }

    /** No empty edge where the picture could fill the screen; centred along a side it doesn't fill. */
    private fun keepInView() {
        val picture = drawable ?: return
        val box = RectF(0f, 0f, picture.intrinsicWidth.toFloat(), picture.intrinsicHeight.toFloat())
        shown.mapRect(box)
        fun along(low: Float, high: Float, size: Int): Float = when {
            high - low <= size -> (size - (high - low)) / 2 - low
            low > 0 -> -low
            high < size -> size - high
            else -> 0f
        }
        shown.postTranslate(along(box.left, box.right, width), along(box.top, box.bottom, height))
    }

    private val pinch = ScaleGestureDetector(context, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(detector: ScaleGestureDetector): Boolean {
            zoomBy(detector.scaleFactor, detector.focusX, detector.focusY)
            return true
        }
    })

    private val touch = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent) = true

        override fun onScroll(e1: MotionEvent?, e2: MotionEvent, dx: Float, dy: Float): Boolean {
            if (zoom <= 1f) return false
            shown.postTranslate(-dx, -dy)
            keepInView()
            imageMatrix = shown
            return true
        }

        override fun onDoubleTap(e: MotionEvent): Boolean {
            if (zoom > 1.01f) fit() else zoomBy(2.5f, e.x, e.y)
            return true
        }

        override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
            onTap?.invoke()
            return true
        }
    })

    @android.annotation.SuppressLint("ClickableViewAccessibility")  // a picture: its gestures have no click to stand for
    override fun onTouchEvent(event: MotionEvent): Boolean {
        pinch.onTouchEvent(event)
        if (!pinch.isInProgress) touch.onTouchEvent(event)
        parent?.requestDisallowInterceptTouchEvent(zoom > 1f || event.pointerCount > 1)
        return true
    }
}

/** A picture in the viewer or an album: an agent's (on the laptop, at url) or yours (its small copy, thumb). */
data class Pic(val name: String, val url: String?, val type: String, val thumb: String?)

/** The viewer's pictures side by side, as a messaging app's: drag sideways and the picture goes with your finger,
 *  the next one coming in beside it; let go past a quarter (or with a flick) and it settles on that one, else it
 *  springs back. Drag down and the picture follows, the dark behind it fading: let go far enough and it closes.
 *  A zoomed picture keeps its fingers to itself (it pans). */
class PicturePager(context: Context, private val count: Int, private val load: (Int, (Bitmap?) -> Unit) -> Unit) :
    android.widget.FrameLayout(context) {
    var index = 0
        private set
    var onIndex: ((Int) -> Unit)? = null  // the picture shown changed
    var onDown: ((Float) -> Unit)? = null  // dragged down, 0..1 of the way to closing
    var onClose: (() -> Unit)? = null
    var onTap: (() -> Unit)? = null
    private val slots = List(3) { ZoomImage(context) }  // the one before, this one, the next one
    private val shownAt = IntArray(3) { -1 }  // which picture each slot holds
    private val slop = android.view.ViewConfiguration.get(context).scaledTouchSlop
    private var downX = 0f
    private var downY = 0f
    private var mode = 0  // 0 undecided, 1 sideways, 2 down
    private var tracker: android.view.VelocityTracker? = null
    private var settling: android.animation.Animator? = null  // a slide to its place, ended by a new touch

    init {
        for (slot in slots) {
            addView(slot, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
            slot.onTap = { onTap?.invoke() }
        }
    }

    fun show(at: Int) {
        index = at.coerceIn(0, count - 1)
        place(0f)
        onIndex?.invoke(index)
    }

    private fun slot(offset: Int) = slots[(((index + offset) % 3) + 3) % 3]

    /** Each slot at its place for a drag of dx, holding its picture (loaded when it first gets it). */
    private fun place(dx: Float) {
        for (offset in -1..1) {
            val slot = slot(offset)
            val picture = index + offset
            slot.visibility = if (picture in 0 until count) VISIBLE else INVISIBLE
            slot.translationX = offset * width.toFloat() + dx
            slot.translationY = 0f
            val i = slots.indexOf(slot)
            if (picture in 0 until count && shownAt[i] != picture) {
                shownAt[i] = picture
                slot.setImageDrawable(null)
                load(picture) { bitmap -> if (shownAt[i] == picture) slot.setImageBitmap(bitmap) }
            }
        }
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        place(0f)
    }

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                settling?.end()  // a new drag starts from where the last one settles
                downX = event.x; downY = event.y; mode = 0
                tracker?.recycle(); tracker = android.view.VelocityTracker.obtain()
                tracker?.addMovement(event)
            }
            MotionEvent.ACTION_MOVE -> {
                tracker?.addMovement(event)
                if (event.pointerCount > 1 || slot(0).zoomed) return false
                val dx = event.x - downX
                val dy = event.y - downY
                if (kotlin.math.abs(dx) > slop && kotlin.math.abs(dx) > kotlin.math.abs(dy) && count > 1) mode = 1
                else if (dy > slop && dy > kotlin.math.abs(dx)) mode = 2
                if (mode != 0) return true
            }
        }
        return false
    }

    @android.annotation.SuppressLint("ClickableViewAccessibility")  // drags only; taps go to the picture
    override fun onTouchEvent(event: MotionEvent): Boolean {
        tracker?.addMovement(event)
        val dx = event.x - downX
        val dy = event.y - downY
        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE -> when (mode) {
                1 -> {  // at the ends it gives a little, then stops
                    val edge = (index == 0 && dx > 0) || (index == count - 1 && dx < 0)
                    place(if (edge) dx / 3 else dx)
                }
                2 -> {
                    val current = slot(0)
                    current.translationY = dy.coerceAtLeast(0f)
                    current.translationX = dx / 2
                    onDown?.invoke((dy / (height * 0.4f)).coerceIn(0f, 1f))
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                tracker?.computeCurrentVelocity(1000)
                val vx = tracker?.xVelocity ?: 0f
                val vy = tracker?.yVelocity ?: 0f
                tracker?.recycle(); tracker = null
                when (mode) {
                    1 -> settle(dx, vx)
                    2 -> if (dy > height * 0.18f || vy > 2500f) onClose?.invoke() else springBack()
                }
                mode = 0
            }
        }
        return true
    }

    private fun settle(dx: Float, vx: Float) {
        val step = when {
            (dx < -width / 4f || vx < -1200f) && index < count - 1 -> 1
            (dx > width / 4f || vx > 1200f) && index > 0 -> -1
            else -> 0
        }
        val target = -step * width.toFloat()
        settling = android.animation.ValueAnimator.ofFloat(slot(0).translationX, target).apply {
            duration = 220; interpolator = android.view.animation.DecelerateInterpolator()
            addUpdateListener { place(it.animatedValue as Float) }
            addListener(object : android.animation.AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: android.animation.Animator) {
                    settling = null
                    if (step != 0) { slot(0).reset(); index += step; onIndex?.invoke(index) }
                    place(0f)
                }
            })
            start()
        }
    }

    private fun springBack() {
        val current = slot(0)
        current.animate().translationX(0f).translationY(0f).setDuration(200)
            .setInterpolator(android.view.animation.DecelerateInterpolator())
            .setUpdateListener { onDown?.invoke((current.translationY / (height * 0.4f)).coerceIn(0f, 1f)) }
            .withEndAction { current.animate().setUpdateListener(null) }.start()  // not left on the close's animation
    }

    /** The picture on screen now, for opening and closing animations. */
    fun current(): ZoomImage = slot(0)
}
