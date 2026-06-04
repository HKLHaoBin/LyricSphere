package com.famyliam.everywhere

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.famyliam.everywhere.databinding.ActivitySetupBinding
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
            startActivity(Intent(this, MainActivity::class.java))
            finish()
        }
    }
}

object ServerPreferences {
    private const val PREFS_NAME = "famyliam_server"
    private const val KEY_URL = "server_url"

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
}
