package com.anmoltyagi.akmobile

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.HapticFeedbackConstants
import android.view.View
import android.view.WindowManager
import android.view.animation.DecelerateInterpolator
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.anmoltyagi.akmobile.databinding.ActivityCallBinding

class CallActivity : AppCompatActivity(), CallClient.Listener {

    private lateinit var binding: ActivityCallBinding
    private var client: CallClient? = null
    private val handler = Handler(Looper.getMainLooper())
    private var seconds = 0
    private var connected = false
    private var muted = false
    private var speakerOn = true

    private val ticker = object : Runnable {
        override fun run() {
            seconds++
            binding.callTimer.text = String.format("%02d:%02d", seconds / 60, seconds % 60)
            handler.postDelayed(this, 1000)
        }
    }

    private val micPerm = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) begin() else showError("Call ke liye mic permission chahiye")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityCallBinding.inflate(layoutInflater)
        setContentView(binding.root)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        binding.orb.setMode(HudOrbView.MODE_LINKING)
        binding.waveform.setMode(WaveformView.MODE_IDLE)
        playEntrance()

        binding.endBtn.setOnClickListener {
            it.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
            hangUp()
        }
        binding.muteBtn.setOnClickListener {
            it.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
            toggleMute()
        }
        binding.speakerBtn.setOnClickListener {
            it.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
            toggleSpeaker()
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            micPerm.launch(Manifest.permission.RECORD_AUDIO)
        } else {
            begin()
        }
    }

    private fun begin() {
        if (client != null) return
        binding.callState.text = getString(R.string.call_connecting)
        binding.hintText.text = getString(R.string.call_hint)
        client = CallClient(applicationContext, this).also { it.start() }
    }

    override fun onStatus(text: String) {
        if (!connected) binding.callState.text = text
    }

    override fun onConnected() {
        if (isFinishing || isDestroyed) return
        connected = true
        binding.orb.setMode(HudOrbView.MODE_LIVE)
        binding.waveform.setMode(WaveformView.MODE_LIVE)
        binding.callState.text = getString(R.string.call_connected)
        binding.callState.setTextColor(ContextCompat.getColor(this, R.color.ok))
        binding.hintText.text = getString(R.string.call_speaking_hint)
        binding.callTimer.visibility = View.VISIBLE
        binding.secureChip.animate().alpha(0.35f).setDuration(120).withEndAction {
            binding.secureChip.animate().alpha(1f).setDuration(260).start()
        }.start()
        seconds = 0
        handler.post(ticker)
    }

    override fun onError(message: String) {
        if (isFinishing || isDestroyed) return
        showError(message)
    }

    private fun showError(message: String) {
        connected = false
        handler.removeCallbacks(ticker)
        binding.orb.setMode(HudOrbView.MODE_ERROR)
        binding.waveform.setMode(WaveformView.MODE_ERROR)
        binding.callState.text = message
        binding.callState.setTextColor(ContextCompat.getColor(this, R.color.danger))
        binding.hintText.text = getString(R.string.call_fail_hint)
    }

    private fun toggleMute() {
        muted = !muted
        client?.setMuted(muted)
        binding.muteBtn.setImageResource(if (muted) R.drawable.ic_mic_off else R.drawable.ic_mic)
        binding.muteBtn.imageTintList = ContextCompat.getColorStateList(
            this, if (muted) R.color.danger else R.color.textPrimary
        )
        binding.muteLabel.setTextColor(
            ContextCompat.getColor(this, if (muted) R.color.danger else R.color.textSecondary)
        )
    }

    private fun toggleSpeaker() {
        speakerOn = !speakerOn
        client?.setSpeaker(speakerOn)
        binding.speakerBtn.imageTintList = ContextCompat.getColorStateList(
            this, if (speakerOn) R.color.primary else R.color.textSecondary
        )
        binding.speakerLabel.setTextColor(
            ContextCompat.getColor(this, if (speakerOn) R.color.primary else R.color.textSecondary)
        )
    }

    private fun hangUp() {
        handler.removeCallbacksAndMessages(null)
        client?.stop()
        client = null
        finish()
    }

    private fun playEntrance() {
        val views = listOf(
            binding.orb,
            binding.callName,
            binding.callState,
            binding.callTimer,
            binding.waveform,
            binding.hintText,
            binding.controls
        )
        val shift = 26f * resources.displayMetrics.density
        views.forEachIndexed { index, view ->
            view.alpha = 0f
            view.translationY = shift
            view.animate()
                .alpha(1f)
                .translationY(0f)
                .setStartDelay(70L * index)
                .setDuration(520L)
                .setInterpolator(DecelerateInterpolator())
                .start()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacksAndMessages(null)
        client?.stop()
        client = null
    }
}
