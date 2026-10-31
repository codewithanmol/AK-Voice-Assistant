package com.anmoltyagi.akmobile

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator

class CyberBackdropView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val glowCyan = Paint(Paint.ANTI_ALIAS_FLAG)
    private val glowMagenta = Paint(Paint.ANTI_ALIAS_FLAG)
    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1.2f
    }
    private val scanPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG)

    private var progress = 0f
    private var w = 0f
    private var h = 0f

    private val animator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 7000L
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            progress = it.animatedValue as Float
            invalidate()
        }
    }

    override fun onSizeChanged(width: Int, height: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(width, height, oldw, oldh)
        w = width.toFloat()
        h = height.toFloat()
        bgPaint.shader = LinearGradient(
            0f, 0f, w, h,
            intArrayOf(0xFF05070D.toInt(), 0xFF0A1424.toInt(), 0xFF070B14.toInt(), 0xFF05070D.toInt()),
            floatArrayOf(0f, 0.45f, 0.75f, 1f),
            Shader.TileMode.CLAMP
        )
        glowCyan.shader = RadialGradient(
            w * 0.15f, h * 0.10f, w * 0.85f,
            intArrayOf(0x2E00E5FF, 0x0E00E5FF, 0x0000E5FF),
            floatArrayOf(0f, 0.45f, 1f),
            Shader.TileMode.CLAMP
        )
        glowMagenta.shader = RadialGradient(
            w * 0.98f, h * 0.80f, w * 0.85f,
            intArrayOf(0x24FF2D95, 0x0AFF2D95, 0x00FF2D95),
            floatArrayOf(0f, 0.5f, 1f),
            Shader.TileMode.CLAMP
        )
        scanPaint.shader = LinearGradient(
            0f, 0f, 0f, dp(110f),
            intArrayOf(0x0000E5FF, 0x3000E5FF, 0x0000E5FF),
            floatArrayOf(0f, 0.5f, 1f),
            Shader.TileMode.CLAMP
        )
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (!animator.isStarted) animator.start()
    }

    override fun onDetachedFromWindow() {
        animator.cancel()
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        canvas.drawRect(0f, 0f, w, h, bgPaint)
        canvas.drawRect(0f, 0f, w, h, glowCyan)
        canvas.drawRect(0f, 0f, w, h, glowMagenta)

        val horizon = h * 0.30f
        val columns = 9
        for (i in -columns..columns) {
            val xBottom = w / 2f + i * (w / (columns * 1.45f))
            gridPaint.color = Color.argb(26, 0, 229, 255)
            canvas.drawLine(w / 2f, horizon, xBottom, h, gridPaint)
        }

        val rows = 15
        for (i in 0..rows) {
            val f = (i / rows.toFloat() + progress * 0.32f) % 1f
            val y = horizon + (h - horizon) * f * f
            gridPaint.color = Color.argb((16 + 40 * f).toInt().coerceIn(0, 255), 0, 229, 255)
            canvas.drawLine(0f, y, w, y, gridPaint)
        }

        linePaint.color = Color.argb(96, 0, 229, 255)
        linePaint.strokeWidth = 1.4f
        canvas.drawLine(0f, horizon, w, horizon, linePaint)

        val sy = (h + dp(140f)) * progress - dp(80f)
        canvas.drawRect(0f, sy, w, sy + dp(110f), scanPaint)
        linePaint.color = Color.argb(60, 0, 229, 255)
        linePaint.strokeWidth = 2f
        canvas.drawLine(0f, sy + dp(54f), w, sy + dp(54f), linePaint)
    }

    private fun dp(value: Float): Float = value * resources.displayMetrics.density
}
