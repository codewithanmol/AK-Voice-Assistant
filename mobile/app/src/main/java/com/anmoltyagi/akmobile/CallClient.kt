package com.anmoltyagi.akmobile

import android.content.Context
import android.media.AudioManager
import android.net.Uri
import android.os.Handler
import android.os.Looper
import java.net.URI
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import org.webrtc.AudioSource
import org.webrtc.AudioTrack
import org.webrtc.DataChannel
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStream
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.RtpTransceiver
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.audio.JavaAudioDeviceModule

class CallClient(private val ctx: Context, private val listener: Listener) {

    interface Listener {
        fun onStatus(text: String)
        fun onConnected()
        fun onError(message: String)
    }

    private val main = Handler(Looper.getMainLooper())
    private val exec = Executors.newSingleThreadExecutor()
    private val audioManager = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val jsonType = "application/json; charset=utf-8".toMediaType()

    private val trustAll = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private val http: OkHttpClient = OkHttpClient.Builder()
        .sslSocketFactory(
            SSLContext.getInstance("TLS").apply {
                init(null, arrayOf<TrustManager>(trustAll), SecureRandom())
            }.socketFactory,
            trustAll
        )
        .hostnameVerifier { _, _ -> true }
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    private var factory: PeerConnectionFactory? = null
    private var pc: PeerConnection? = null
    private var source: AudioSource? = null
    private var localTrack: AudioTrack? = null
    private var adm: JavaAudioDeviceModule? = null

    @Volatile private var stopped = false
    @Volatile private var notified = false

    fun start() {
        exec.execute {
            try {
                doStart()
            } catch (e: Exception) {
                if (!stopped) fail(e.message ?: "Call start nahi hua")
            }
        }
    }

    fun stop() {
        stopped = true
        exec.execute { teardown() }
    }

    fun setMuted(muted: Boolean) {
        exec.execute { runCatching { localTrack?.setEnabled(!muted) } }
    }

    fun setSpeaker(on: Boolean) {
        exec.execute {
            runCatching {
                if (on) audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
                audioManager.isSpeakerphoneOn = on
            }
        }
    }

    private fun doStart() {
        status("Laptop se contact kar raha hun…")
        var token = Prefs.token(ctx)
        if (token.isBlank()) {
            if (!ApiClient(ctx).pair()) {
                throw IllegalStateException("Phone paired nahi hai. Settings me server check karo.")
            }
            token = Prefs.token(ctx)
        }
        if (token.isBlank()) throw IllegalStateException("Pairing token nahi mila")

        val trigger = post("${callOrigin()}/api/call/trigger", token, "{}")
        val callUrl = trigger.optString("call_url")
        if (callUrl.isBlank()) throw IllegalStateException("Server ne call URL nahi diya")
        val session = trigger.optString("session_id")
        val parsed = Uri.parse(callUrl)
        val callToken = parsed.getQueryParameter("token")
            ?: throw IllegalStateException("Call token missing")
        val origin = "https://${parsed.host}:${parsed.port}"
        if (stopped) return

        status("Mic aur audio taiyaar kar raha hun…")
        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(ctx).createInitializationOptions()
        )
        val audio = JavaAudioDeviceModule.builder(ctx)
            .setUseHardwareAcousticEchoCanceler(true)
            .setUseHardwareNoiseSuppressor(true)
            .createAudioDeviceModule()
        adm = audio
        val f = PeerConnectionFactory.builder()
            .setAudioDeviceModule(audio)
            .createPeerConnectionFactory()
        factory = f

        val config = PeerConnection.RTCConfiguration(emptyList())
        config.sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
        val conn = f.createPeerConnection(config, observer)
            ?: throw IllegalStateException("WebRTC peer ban nahi paya")
        pc = conn

        val src = f.createAudioSource(MediaConstraints())
        source = src
        val track = f.createAudioTrack("ak-mic", src)
        localTrack = track
        conn.addTrack(track, listOf("ak-audio"))
        runCatching {
            audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
            audioManager.isSpeakerphoneOn = true
        }

        val constraints = MediaConstraints().apply {
            mandatory.add(MediaConstraints.KeyValuePair("OfferToReceiveAudio", "true"))
        }
        val offerLatch = CountDownLatch(1)
        var offer: SessionDescription? = null
        conn.createOffer(object : SdpAdapter() {
            override fun onCreateSuccess(desc: SessionDescription?) {
                offer = desc
                offerLatch.countDown()
            }

            override fun onCreateFailure(err: String?) {
                offer = null
                offerLatch.countDown()
            }
        }, constraints)
        offerLatch.await(6, TimeUnit.SECONDS)
        val sdp = offer ?: throw IllegalStateException("Offer create nahi hua")

        val localLatch = CountDownLatch(1)
        conn.setLocalDescription(object : SdpAdapter() {
            override fun onSetSuccess() = localLatch.countDown()
            override fun onSetFailure(err: String?) = localLatch.countDown()
        }, sdp)
        localLatch.await(4, TimeUnit.SECONDS)

        waitIce(conn)
        if (stopped) return
        val local = conn.localDescription ?: throw IllegalStateException("Local SDP missing")

        status("AK ko ring kar raha hun…")
        val body = JSONObject()
            .put("session_id", session)
            .put(
                "sdp",
                JSONObject()
                    .put("type", "offer")
                    .put("sdp", local.description)
            )
            .toString()
        val response = post("$origin/api/call/offer", callToken, body)
        val answer = response.optJSONObject("answer")
            ?: throw IllegalStateException("Server ne answer nahi bheja")
        val answerSdp = answer.optString("sdp")
        if (answerSdp.isBlank()) throw IllegalStateException("Answer SDP khaali hai")

        val remoteLatch = CountDownLatch(1)
        conn.setRemoteDescription(object : SdpAdapter() {
            override fun onSetSuccess() = remoteLatch.countDown()
            override fun onSetFailure(err: String?) = remoteLatch.countDown()
        }, SessionDescription(SessionDescription.Type.ANSWER, answerSdp))
        remoteLatch.await(4, TimeUnit.SECONDS)
    }

    private fun waitIce(conn: PeerConnection, timeoutMs: Long = 3000) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!stopped &&
            conn.iceGatheringState() != PeerConnection.IceGatheringState.COMPLETE &&
            System.currentTimeMillis() < end
        ) {
            Thread.sleep(50)
        }
    }

    private fun callOrigin(): String {
        val uri = URI(Prefs.serverUrl(ctx))
        val host = uri.host ?: throw IllegalStateException("Server URL galat hai")
        val port = when {
            uri.scheme.equals("https", true) && uri.port == 4098 -> 4098
            uri.port > 0 -> uri.port + 1
            else -> 4098
        }
        return "https://$host:$port"
    }

    private fun post(url: String, token: String, body: String): JSONObject {
        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .post(body.toRequestBody(jsonType))
            .build()
        http.newCall(request).execute().use { res ->
            val raw = res.body?.string().orEmpty()
            val parsed = runCatching { JSONObject(raw) }.getOrNull()
            if (!res.isSuccessful) {
                val detail = parsed?.optString("error").orEmpty().ifBlank { raw.take(140) }
                throw IllegalStateException("Server error ${res.code}: $detail")
            }
            return parsed ?: JSONObject()
        }
    }

    private val observer = object : PeerConnection.Observer {
        override fun onSignalingChange(state: PeerConnection.SignalingState?) {}
        override fun onIceConnectionChange(state: PeerConnection.IceConnectionState?) {
            if (state == PeerConnection.IceConnectionState.CONNECTED && !notified) {
                notified = true
                main.post { listener.onConnected() }
            }
            if (state == PeerConnection.IceConnectionState.FAILED && !stopped) {
                main.post { listener.onError("Call connect nahi hua. Tailscale ON hai?") }
            }
        }

        override fun onIceConnectionReceivingChange(receiving: Boolean) {}
        override fun onIceGatheringChange(state: PeerConnection.IceGatheringState?) {}
        override fun onIceCandidate(candidate: IceCandidate?) {}
        override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>?) {}
        override fun onAddStream(stream: MediaStream?) {}
        override fun onRemoveStream(stream: MediaStream?) {}
        override fun onDataChannel(channel: DataChannel?) {}
        override fun onRenegotiationNeeded() {}
        override fun onAddTrack(receiver: RtpReceiver?, streams: Array<out MediaStream>?) {}
        override fun onTrack(transceiver: RtpTransceiver?) {}
    }

    private fun status(text: String) {
        if (!stopped) main.post { listener.onStatus(text) }
    }

    private fun fail(message: String) {
        stopped = true
        teardown()
        main.post { listener.onError(message) }
    }

    private fun teardown() {
        runCatching { localTrack?.setEnabled(false) }
        runCatching { pc?.close() }
        runCatching { pc?.dispose() }
        runCatching { localTrack?.dispose() }
        runCatching { source?.dispose() }
        runCatching { adm?.release() }
        runCatching { factory?.dispose() }
        runCatching {
            audioManager.isSpeakerphoneOn = false
            audioManager.mode = AudioManager.MODE_NORMAL
        }
        pc = null
        localTrack = null
        source = null
        adm = null
        factory = null
    }

    private open class SdpAdapter : SdpObserver {
        override fun onCreateSuccess(desc: SessionDescription?) {}
        override fun onSetSuccess() {}
        override fun onCreateFailure(err: String?) {}
        override fun onSetFailure(err: String?) {}
    }
}
