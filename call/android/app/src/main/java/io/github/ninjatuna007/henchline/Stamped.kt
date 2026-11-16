package io.github.ninjatuna007.henchline

import android.content.Context
import android.graphics.Canvas
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.widget.TextView

/**
 * A chat bubble whose time (and, on yours, its ticks) sits in its bottom-right corner, as a messaging app's does
 * (the user's wish, 2026-11-16: following the last word, it landed all over the place). The words end with an
 * invisible copy of the time, so the last line keeps room for it, or wraps to a line of its own; the time itself is
 * drawn at the corner, on the last line's baseline.
 */
class Stamped(context: Context) : TextView(context) {
    var tail: CharSequence? = null
        set(value) { field = value; drawn = null; invalidate() }
    private var drawn: StaticLayout? = null

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val tail = tail ?: return
        val text = layout ?: return
        val paint = TextPaint(paint).apply { textSize = this@Stamped.textSize }  // the spans scale it, as in the words
        val laid = drawn ?: StaticLayout.Builder.obtain(tail, 0, tail.length, paint, 10_000)
            .setAlignment(Layout.Alignment.ALIGN_NORMAL).setIncludePad(false).build().also { drawn = it }
        val width = laid.getLineWidth(0)
        val baseline = totalPaddingTop + text.getLineBaseline(text.lineCount - 1)
        canvas.save()
        canvas.translate(this.width - totalPaddingRight - width, (baseline - laid.getLineBaseline(0)).toFloat())
        laid.draw(canvas)
        canvas.restore()
    }
}
