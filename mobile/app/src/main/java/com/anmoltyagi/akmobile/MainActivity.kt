package com.anmoltyagi.akmobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.view.View
import android.view.animation.AnimationUtils
import android.view.animation.OvershootInterpolator
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.anmoltyagi.akmobile.databinding.ActivityMainBinding
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import android.animation.Animator
import android.animation.AnimatorSet
import android.animation.ObjectAnimator
import android.graphics.Color

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var api: ApiClient
    private lateinit var adapter: MessageAdapter
    private val messages = mutableListOf<Message>()
    private var speech: SpeechRecognizer? = null
    private var listening = false
    private var dotAnimators: MutableList<Animator> = mutableListOf()

    private val micPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startListening() else toast("Mic permission required for voice")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        api = ApiClient(this)
        adapter = MessageAdapter(messages)

        binding.messageList.layoutManager = LinearLayoutManager(this)
        binding.messageList.adapter = adapter

        if (messages.isEmpty()) {
            adapter.add(Message(
                "Radhe Radhe boss! Main AK hoon. Laptop se juda hua hun. Kuch bhi puchho — main solve kar dunga. 😎",
                false,
                now()
            ))
        }

        binding.sendBtn.setOnClickListener { send() }
        binding.micBtn.setOnClickListener { onMicTap() }
        binding.callBtn.setOnClickListener {
            startActivity(Intent(this, CallActivity::class.java))
        }
        binding.settingsBtn.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        binding.statusText.setOnClickListener { checkHealth() }

        setupDotAnimation()
        checkHealth()
    }

    override fun onResume() {
        super.onResume()
        checkHealth()
    }

    override fun onDestroy() {
        super.onDestroy()
        dotAnimators.forEach { it.cancel() }
        speech?.destroy()
    }

    private fun setupDotAnimation() {
        val dots = listOf(binding.dot1, binding.dot2, binding.dot3)
        dots.forEachIndexed { idx, dot ->
            val anim = ObjectAnimator.ofFloat(dot, View.TRANSLATION_Y, 0f, -8f, 0f)
            anim.duration = 520L
            anim.startDelay = idx * 160L
            anim.repeatCount = ObjectAnimator.INFINITE
            anim.repeatMode = ObjectAnimator.RESTART
            anim.interpolator = OvershootInterpolator(2.0f)
            dotAnimators.add(anim)
        }
    }

    private fun pulseStatus(view: View) {
        val scale = ObjectAnimator.ofFloat(view, View.SCALE_X, 1f, 1.35f, 1f)
        val scaleY = ObjectAnimator.ofFloat(view, View.SCALE_Y, 1f, 1.35f, 1f)
        scale.duration = 1100
        scaleY.duration = 1100
        scale.repeatCount = ObjectAnimator.INFINITE
        scaleY.repeatCount = ObjectAnimator.INFINITE
        val set = AnimatorSet()
        set.playTogether(scale, scaleY)
        set.start()
    }

    private fun pop(view: View) {
        view.animate()
            .scaleX(0.85f).scaleY(0.85f)
            .setDuration(90)
            .withEndAction {
                view.animate()
                    .scaleX(1f).scaleY(1f)
                    .setInterpolator(OvershootInterpolator(3f))
                    .setDuration(200)
                    .start()
            }
            .start()
    }

    private fun now(): String =
        SimpleDateFormat("hh:mm a", Locale.getDefault()).format(Date())

    /** Pehli baar/token na ho to server se auto-pair ho jao (Tailscale se). */
    private fun ensureToken(): Boolean {
        if (Prefs.token(this).isNotBlank()) return true
        return try { api.pair() } catch (e: Exception) { false }
    }

    private fun send() {
        val text = binding.input.text.toString().trim()
        if (text.isEmpty()) return
        binding.input.setText("")
        adapter.add(Message(text, true, now()))
        pop(binding.sendBtn)
        setThinking(true)
        lifecycleScope.launch {
            val reply = withContext(Dispatchers.IO) {
                ensureToken()
                try {
                    api.sendMessage(text)
                } catch (e: Exception) {
                    "Connection fail hua boss. Laptop pe ak_server.py chalu hai na? 🛠️"
                }
            }
            setThinking(false)
            adapter.add(Message(reply, false, now()))
            scrollToBottom()
            checkHealth()
        }
    }

    private fun setThinking(on: Boolean) {
        if (on) {
            binding.thinking.visibility = View.VISIBLE
            binding.sendBtn.isEnabled = false
            dotAnimators.forEach { it.start() }
        } else {
            binding.thinking.visibility = View.GONE
            binding.sendBtn.isEnabled = true
            dotAnimators.forEach { it.cancel() }
        }
    }

    private fun scrollToBottom() {
        binding.messageList.post {
            binding.messageList.scrollToPosition(adapter.itemCount - 1)
        }
    }

    private fun setStatus(ok: Boolean?) {
        val dot = binding.statusDot
        val label = binding.statusText
        when (ok) {
            true -> {
                dot.background = ContextCompat.getDrawable(this, R.drawable.bg_status_dot)
                label.text = "Connected"
                label.setTextColor(getColor(R.color.ok))
                pulseStatus(dot)
            }
            false -> {
                dot.background = ContextCompat.getDrawable(this, R.drawable.bg_status_dot_bad)
                label.text = "Offline"
                label.setTextColor(getColor(R.color.bad))
            }
            null -> {
                dot.background = ContextCompat.getDrawable(this, R.drawable.bg_status_dot_warn)
                label.text = "Connecting…"
                label.setTextColor(getColor(R.color.warn))
            }
        }
    }

    private fun checkHealth() {
        setStatus(null)
        lifecycleScope.launch {
            val ok = withContext(Dispatchers.IO) {
                if (Prefs.token(this@MainActivity).isBlank()) api.pair()
                api.health()
            }
            setStatus(ok)
        }
    }

    private fun onMicTap() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            micPermLauncher.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        startListening()
    }

    private fun startListening() {
        if (listening) return
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            toast("Speech recognition not available")
            return
        }
        speech?.destroy()
        speech = SpeechRecognizer.createSpeechRecognizer(this)
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        }
        speech?.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                listening = false
                binding.micBtn.setImageResource(R.drawable.ic_mic)
                val spoken = matches?.firstOrNull()?.trim()
                if (!spoken.isNullOrEmpty()) {
                    binding.input.setText(spoken)
                    send()
                }
            }

            override fun onReadyForSpeech(params: Bundle?) { listening = true }
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() { listening = false }
            override fun onError(error: Int) {
                listening = false
                binding.micBtn.setImageResource(R.drawable.ic_mic)
                toast("Voice: bol nahi suna")
            }
            override fun onPartialResults(partialResults: Bundle?) {
                val p = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                p?.firstOrNull()?.let { binding.input.setText(it) }
            }
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        binding.micBtn.setImageResource(R.drawable.ic_mic_filled)
        speech?.startListening(intent)
    }

    private fun toast(t: String) = Toast.makeText(this, t, Toast.LENGTH_SHORT).show()

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
    }
}