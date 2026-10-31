package com.anmoltyagi.akmobile

import android.content.Context

object Prefs {
    /** Empty until the user enters their laptop's address in Settings. */
    fun serverUrl(ctx: Context): String =
        CryptoPrefs.get(ctx, "server_url", "")

    fun setServerUrl(ctx: Context, url: String) =
        CryptoPrefs.put(ctx, "server_url", url.trim().trimEnd('/'))

    /** Server password set by the user; never stored in plaintext. */
    fun serverPassword(ctx: Context): String =
        CryptoPrefs.get(ctx, "server_password", "")

    fun setServerPassword(ctx: Context, pw: String) =
        CryptoPrefs.put(ctx, "server_password", pw.trim())

    /** Install-unique id — server se auto-pair ke liye. */
    fun installId(ctx: Context): String {
        var id = CryptoPrefs.get(ctx, "install_id", "")
        if (id.isBlank()) {
            id = "ak_phone_" + java.util.UUID.randomUUID().toString().replace("-", "")
            CryptoPrefs.put(ctx, "install_id", id)
        }
        return id
    }

    /** Server se auto-pair hue token (Bearer). Password phone pe kabhi nahi. */
    fun token(ctx: Context): String =
        CryptoPrefs.get(ctx, "app_token", "")

    fun setToken(ctx: Context, t: String) =
        CryptoPrefs.put(ctx, "app_token", t)
}