package com.anmoltyagi.akmobile

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

class HudOrbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    companion object {
        const val MODE_IDLE = 0
        const val MODE_LINKING = 1
        const val MODE_LIVE = 2
        const val MODE_ERROR = 3
    }

    private var mode = MODE_IDLE
    private val born = System.nanoTime()

    private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val corePaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val tickPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        textAlign = Paint.Align.CENTER
    }
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG)

    private var cx = 0f
    private var cy = 0f
    private var radius = 0f
    private val arcRect = RectF()

    init {
        setLayerType(LAYER_TYPE_SOFTWARE, null)
    }

    fun setMode(value: Int) {
        if (mode == value) return
        mode = value
        rebuildShaders()
        postInvalidateOnAnimation()
    }

    override fun onSizeChanged(width: Int, height: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(width, height, oldw, oldh)
        cx = width / 2f
        cy = height / 2f
        radius = min(width, height) / 2f * 0.86f
        textPaint.textSize = radius * 0.46f
        arcRect.set(cx - radius * 0.82f, cy - radius * 0.82f, cx + radius * 0.82f, cy + radius * 0.82f)
        rebuildShaders()
    }

    private fun rebuildShaders() {
        if (radius <= 0f) return
        val accent = accent()
        glowPaint.shader = RadialGradient(
            cx, cy, radius * 1.35f,
            intArrayOf(alpha(accent, 0x4C), alpha(accent, 0x16), Color.TRANSPARENT),
            floatArrayOf(0f, 0.5f, 1f),
            Shader.TileMode.CLAMP
        )
        val inner = when (mode) {
            MODE_LIVE -> 0xFF145062.toInt()
            MODE_ERROR -> 0xFF431A26.toInt()
            MODE_LINKING -> 0xFF3B3220.toInt()
            else -> 0xFF0E2A3A.toInt()
        }
        corePaint.shader = RadialGradient(
            cx, cy - radius * 0.12f, radius * 0.78f,
            intArrayOf(inner, 0xFF0A1726.toInt(), 0xFF060B14.toInt()),
            floatArrayOf(0f, 0.68f, 1f),
            Shader.TileMode.CLAMP
        )
        textPaint.setShadowLayer(radius * 0.22f, 0f, 0f, alpha(accent, 0xB0))
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        postInvalidateOnAnimation()
    }

    override fun onDraw(canvas: Canvas) {
        val t = (System.nanoTime() - born) / 1_000_000_000f
        val accent = accent()
        val live = mode == MODE_LIVE
        val linking = mode == MODE_LINKING

        val pulse = if (live) 1f + 0.035f * sin(t * 3.4f) else 1f
        canvas.drawCircle(cx, cy, radius * 1.32f * pulse, glowPaint)

        ringPaint.color = alpha(accent, 0x33)
        ringPaint.strokeWidth = dp(1.2f)
        canvas.drawCircle(cx, cy, radius * 0.96f, ringPaint)

        val ticks = 72
        tickPaint.strokeWidth = dp(1.4f)
        for (i in 0 until ticks) {
            val angle = Math.toRadians(i * 360.0 / ticks)
            val wobble = 0.5f + 0.5f * sin(t * 2.2f + i * 0.35f)
            val len = dp(4f) + dp(7f) * wobble * (if (live) 1f else 0.35f)
            val r1 = radius * 0.90f
            val r2 = r1 + len
            tickPaint.color = alpha(accent, (if (live) 120 else 60) - (i % 6) * 6)
            canvas.drawLine(
                cx + cos(angle).toFloat() * r1, cy + sin(angle).toFloat() * r1,
                cx + cos(angle).toFloat() * r2, cy + sin(angle).toFloat() * r2,
                tickPaint
            )
        }

        arcPaint.strokeWidth = dp(2.2f)
        for (k in 0..2) {
            arcPaint.color = alpha(accent, if (k == 0) 0xD8 else if (k == 1) 0x80 else 0x44)
            val speed = 12f + k * 21f
            val dir = if (k % 2 == 1) -1f else 1f
            val sweep = 62f + 26f * sin(t * 1.4f + k)
            canvas.save()
            canvas.rotate(t * speed * dir + k * 118f, cx, cy)
            canvas.drawArc(arcRect, 0f, sweep, false, arcPaint)
            canvas.restore()
        }

        canvas.drawCircle(cx, cy, radius * 0.74f * pulse, corePaint)

        ringPaint.color = alpha(accent, if (live) 0xAA else 0x66)
        ringPaint.strokeWidth = dp(1.6f)
        canvas.drawCircle(cx, cy, radius * 0.74f * pulse, ringPaint)

        dotPaint.color = alpha(accent, 0xCC)
        val dotR = radius * 0.05f
        val orbit = radius * 0.74f * pulse
        val orbitAngle = t * (if (linking) 3.6f else 1.4f)
        canvas.drawCircle(
            cx + cos(orbitAngle.toDouble()).toFloat() * orbit,
            cy + sin(orbitAngle.toDouble()).toFloat() * orbit,
            dotR, dotPaint
        )

        textPaint.color = alpha(accent, 0xFF)
        canvas.drawText("AK", cx, cy + textPaint.textSize * 0.35f, textPaint)

        if (isAttachedToWindow) postInvalidateOnAnimation()
    }

    private fun accent(): Int = when (mode) {
        MODE_LIVE -> 0xFF00E5FF.toInt()
        MODE_LINKING -> 0xFFFFC24D.toInt()
        MODE_ERROR -> 0xFFFF4D6D.toInt()
        else -> 0xFF2E7F99.toInt()
    }

    private fun alpha(color: Int, value: Int): Int =
        Color.argb(value.coerceIn(0, 255), Color.red(color), Color.green(color), Color.blue(color))

    private fun dp(value: Float): Float = value * resources.displayMetrics.density
}
