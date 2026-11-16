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
 *  A single tap is the screen's (it shows or hides the bar); a flick sideways, not zoomed, the next picture. */
class ZoomImage(context: Context) : ImageView(context) {
    private val shown = Matrix()
    private var zoom = 1f  // over the fitted size
    var onTap: (() -> Unit)? = null
    var onSwipe: ((Int) -> Unit)? = null  // +1 the next picture, -1 the one before (a flick while not zoomed)

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

        override fun onFling(e1: MotionEvent?, e2: MotionEvent, vx: Float, vy: Float): Boolean {
            if (zoom > 1.01f || kotlin.math.abs(vx) < 800f || kotlin.math.abs(vx) < kotlin.math.abs(vy)) return false
            onSwipe?.invoke(if (vx < 0) 1 else -1)
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
