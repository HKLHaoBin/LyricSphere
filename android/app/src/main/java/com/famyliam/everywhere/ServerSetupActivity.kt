package com.famyliam.everywhere

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.browser.customtabs.CustomTabsIntent
import com.famyliam.everywhere.databinding.ActivitySetupBinding
import com.famyliam.everywhere.util.BatteryOptimizationHelper
import com.famyliam.everywhere.util.ServerUrlNormalizer

class ServerSetupActivity : AppCompatActivity() {
    private lateinit var binding: ActivitySetupBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val existing = ServerPreferences.getServerUrl(this)
        if (!existing.isNullOrBlank() && intent?.action == Intent.ACTION_MAIN) {
            startActivity(Intent(this, MainActivity::class.java))
            finish()
            return
        }

        binding = ActivitySetupBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ServerPreferences.getServerUrl(this)?.let { saved ->
            binding.serverUrlInput.setText(saved)
        }

        when (ServerPreferences.getForegroundResumeMode(this)) {
            ServerPreferences.FOREGROUND_RESUME_NATIVE ->
                binding.resumeModeNative.isChecked = true
            else ->
                binding.resumeModeWeb.isChecked = true
        }

        binding.saveButton.setOnClickListener {
            val raw = binding.serverUrlInput.text?.toString().orEmpty()
            if (raw.isBlank()) {
                Toast.makeText(this, R.string.setup_error_empty, Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val normalized = ServerUrlNormalizer.normalize(raw)
            if (!normalized.startsWith("http")) {
                Toast.makeText(this, R.string.setup_error_invalid, Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            ServerPreferences.saveServerUrl(this, normalized)
            val resumeMode = if (binding.resumeModeNative.isChecked) {
                ServerPreferences.FOREGROUND_RESUME_NATIVE
            } else {
                ServerPreferences.FOREGROUND_RESUME_WEB
            }
            ServerPreferences.saveForegroundResumeMode(this, resumeMode)
            startActivity(Intent(this, MainActivity::class.java))
            finish()
        }

        binding.batteryOptimizationButton.setOnClickListener {
            if (!BatteryOptimizationHelper.requestIgnoreBatteryOptimizations(this)) {
                Toast.makeText(this, R.string.setup_battery_failed, Toast.LENGTH_SHORT).show()
            }
        }

        binding.openInBrowserButton.setOnClickListener {
            openServerInCustomTab()
        }
    }

    private fun openServerInCustomTab() {
        val raw = binding.serverUrlInput.text?.toString().orEmpty()
        val url = ServerUrlNormalizer.normalize(raw).ifBlank {
            ServerPreferences.getServerUrl(this).orEmpty()
        }
        if (!url.startsWith("http")) {
            Toast.makeText(this, R.string.setup_error_invalid, Toast.LENGTH_SHORT).show()
            return
        }
        try {
            CustomTabsIntent.Builder().build().launchUrl(this, Uri.parse(url))
        } catch (_: Exception) {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        }
    }
}

object ServerPreferences {
    private const val PREFS_NAME = "famyliam_server"
    private const val KEY_URL = "server_url"
    private const val KEY_FOREGROUND_RESUME_MODE = "foreground_resume_mode"

    const val FOREGROUND_RESUME_WEB = "web_audio"
    const val FOREGROUND_RESUME_NATIVE = "native_audio"

    fun getServerUrl(context: Context): String? {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(KEY_URL, null)
            ?.takeIf { it.isNotBlank() }
    }

    fun saveServerUrl(context: Context, url: String) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_URL, url)
            .apply()
    }

    fun getForegroundResumeMode(context: Context): String {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(KEY_FOREGROUND_RESUME_MODE, FOREGROUND_RESUME_WEB)
            ?: FOREGROUND_RESUME_WEB
    }

    fun saveForegroundResumeMode(context: Context, mode: String) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_FOREGROUND_RESUME_MODE, mode)
            .apply()
    }
}
