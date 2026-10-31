package com.anmoltyagi.akmobile

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Password/URL ko Android Keystore se encrypt karke rakhta hai — plaintext
 * file me nahi dikhta (run-as se bhi secret nahi chhup jata).
 */
object CryptoPrefs {
    private const val ALIAS = "ak_mobile_key"
    private const val ENC_NAME = "ak_mobile_enc"
    private const val LEGACY_NAME = "ak_mobile"
    private const val GCM_TAG = 128

    private fun encSp(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(ENC_NAME, Context.MODE_PRIVATE)

    private fun legacySp(ctx: Context): SharedPreferences? =
        try {
            ctx.getSharedPreferences(LEGACY_NAME, Context.MODE_PRIVATE)
        } catch (e: Exception) {
            null
        }

    private fun getOrCreateKey(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore"
        )
        gen.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return gen.generateKey()
    }

    private fun encrypt(plain: String, key: SecretKey): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key)
        val iv = cipher.iv
        val ct = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        val out = ByteArray(iv.size + ct.size)
        System.arraycopy(iv, 0, out, 0, iv.size)
        System.arraycopy(ct, 0, out, iv.size, ct.size)
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    private fun decrypt(enc: String, key: SecretKey): String? {
        return try {
            val raw = Base64.decode(enc, Base64.NO_WRAP)
            if (raw.size <= 12) {
                null
            } else {
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(
                    Cipher.DECRYPT_MODE, key,
                    GCMParameterSpec(GCM_TAG, raw, 0, 12)
                )
                String(cipher.doFinal(raw, 12, raw.size - 12), Charsets.UTF_8)
            }
        } catch (e: Exception) {
            null
        }
    }

    fun put(ctx: Context, key: String, value: String) {
        try {
            val enc = encrypt(value, getOrCreateKey())
            encSp(ctx).edit().putString(key, enc).apply()
        } catch (e: Exception) {
            // never store plaintext — fail silently, keep old value
        }
    }

    fun get(ctx: Context, key: String, fallback: String = ""): String {
        try {
            val enc = encSp(ctx).getString(key, null)
            if (enc != null) {
                decrypt(enc, getOrCreateKey())?.let { return it }
            }
        } catch (e: Exception) {
            return fallback
        }
        // Legacy plaintext migration (first run after update)
        try {
            val old = legacySp(ctx) ?: return fallback
            val raw = old.getString(key, null)
            if (raw != null) {
                put(ctx, "server_url", old.getString("server_url", "") ?: "")
                put(ctx, "pass", old.getString("pass", "") ?: "")
                old.edit().clear().apply()
                runCatching {
                    File(ctx.applicationInfo.dataDir,
                         "shared_prefs/$LEGACY_NAME.xml").delete()
                }
                return raw
            }
        } catch (e: Exception) {
            return fallback
        }
        return fallback
    }
}