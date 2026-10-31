package com.anmoltyagi.akmobile

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.anmoltyagi.akmobile.databinding.ActivitySettingsBinding

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        binding.serverUrl.setText(Prefs.serverUrl(this))
        binding.serverPassword.setText(Prefs.serverPassword(this))

        binding.saveBtn.setOnClickListener {
            val url = binding.serverUrl.text.toString().trim().trimEnd('/')
            if (!url.startsWith("http")) {
                Toast.makeText(this, "URL http:// se shuru hona chahiye", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            Prefs.setServerUrl(this, url)
            Prefs.setServerPassword(this, binding.serverPassword.text.toString())
            Toast.makeText(this, "Settings save ho gaye", Toast.LENGTH_SHORT).show()
            finish()
        }

        binding.testBtn.setOnClickListener {
            binding.testBtn.text = "Testing..."
            binding.testBtn.isEnabled = false
            val api = ApiClient(this)
            Thread {
                val ok = api.health()
                runOnUiThread {
                    binding.testBtn.text = ok.takeIf { it }?.let { "Connected ✓" } ?: "No reach"
                    binding.testBtn.isEnabled = true
                    Toast.makeText(
                        this,
                        if (ok) "Server mil gaya!" else "Server nahi mila — IP check karo, aur Tailscale ON hona chahiye",
                        Toast.LENGTH_SHORT
                    ).show()
                }
            }.start()
        }
    }
}