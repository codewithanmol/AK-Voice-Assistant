package com.anmoltyagi.akmobile

import android.content.Context
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit
import org.json.JSONObject

class ApiClient(private val ctx: Context) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private fun base(): String = Prefs.serverUrl(ctx)

    private fun auth(): String {
        val token = Prefs.token(ctx)
        return if (token.isNotBlank()) "Bearer $token" else ""
    }

    /** Server se auto-pair — install_id de kar device token le leta hai. */
    fun pair(): Boolean {
        try {
            val body = JSONObject()
                .put("install_id", Prefs.installId(ctx))
                .put("password", Prefs.serverPassword(ctx))
                .toString()
            val reqBody: RequestBody = body.toRequestBody("application/json".toMediaType())
            val builder = Request.Builder().url("${base()}/api/pair").post(reqBody)
            client.newCall(builder.build()).execute().use { res ->
                if (!res.isSuccessful) return false
                val raw = res.body?.string() ?: return false
                val token = JSONObject(raw).optString("token", "")
                if (token.isBlank()) return false
                Prefs.setToken(ctx, token)
                return true
            }
        } catch (e: Exception) {
            return false
        }
    }

    fun health(): Boolean {
        try {
            val builder = Request.Builder().url("${base()}/health").get()
            if (auth().isNotEmpty()) builder.header("Authorization", auth())
            client.newCall(builder.build()).execute().use { res ->
                return res.isSuccessful
            }
        } catch (e: Exception) {
            return false
        }
    }

    @Throws(Exception::class)
    fun sendMessage(text: String): String {
        val bodyJson = JSONObject().put("message", text).toString()
        val body: RequestBody = bodyJson.toRequestBody("application/json".toMediaType())
        val builder = Request.Builder().url("${base()}/chat").post(body)
        if (auth().isNotEmpty()) builder.header("Authorization", auth())
        client.newCall(builder.build()).execute().use { res ->
            if (!res.isSuccessful) {
                throw Exception("Server error ${res.code}")
            }
            val raw = res.body?.string() ?: "{}"
            return JSONObject(raw).optString("reply", "")
        }
    }
}