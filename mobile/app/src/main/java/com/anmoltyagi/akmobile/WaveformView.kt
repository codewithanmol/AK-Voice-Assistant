package com.anmoltyagi.akmobile

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin

class WaveformView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    companion object {
        const val MODE_IDLE = 0
        const val MODE_LIVE = 1
        const val MODE_ERROR = 2
    }

    private var mode = MODE_IDLE
    private val born = System.nanoTime()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private var level = 0f
    private var gradient: Shader? = null

    fun setMode(value: Int) {
        mode = value
        postInvalidateOnAnimation()
    }

    override fun onSizeChanged(width: Int, height: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(width, height, oldw, oldh)
        gradient = LinearGradient(
            0f, 0f, width.toFloat(), 0f,
            intArrayOf(0xFF00E5FF.toInt(), 0xFFB388FF.toInt(), 0xFFFF2D95.toInt()),
            floatArrayOf(0f, 0.55f, 1f),
            Shader.TileMode.CLAMP
        )
    }

    override fun onDraw(canvas: Canvas) {
        val t = (System.nanoTime() - born) / 1_000_000_000f
        val target = when (mode) {
            MODE_LIVE -> 1f
            MODE_ERROR -> 0.28f
            else -> 0.10f
        }
        level += (target - level) * 0.09f

        val bars = 30
        val gap = width.toFloat() / bars
        val mid: Float = height / 2f
        paint.strokeWidth = gap * 0.42f
        paint.shader = if (mode == MODE_ERROR) null else gradient
        if (mode == MODE_ERROR) paint.color = Color.argb(180, 255, 77, 109)

        for (i in 0 until bars) {
            val f = i / (bars - 1f)
            val envelope = sin((f * PI).toFloat()).toFloat()
            val osc = (0.30f + 0.70f * abs(
                sin(t * 5.1f + i * 0.53f) * 0.62f + sin(t * 9.7f + i * 0.91f) * 0.38f
            )).toFloat()
            val h: Float = height * (0.05f + 0.42f * envelope * osc * level)
            val x = gap * i + gap / 2f
            canvas.drawLine(x, mid - h, x, mid + h, paint)
        }

        if (isAttachedToWindow) postInvalidateOnAnimation()
    }
}
