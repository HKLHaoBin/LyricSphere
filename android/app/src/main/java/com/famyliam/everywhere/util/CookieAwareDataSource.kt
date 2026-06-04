package com.famyliam.everywhere.util

import android.content.Context
import android.webkit.CookieManager
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource

/**
 * Media3 [DataSource.Factory] that injects WebView [CookieManager] cookies into HTTP requests.
 */
class CookieAwareDataSource(
    private val context: Context,
    private val cookieUrlSupplier: () -> String
) : DataSource.Factory {

    override fun createDataSource(): DataSource {
        val cookieUrl = cookieUrlSupplier().ifBlank { "http://127.0.0.1" }
        val cookieHeader = CookieManager.getInstance().getCookie(cookieUrl).orEmpty()
        val httpFactory = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(true)
        if (cookieHeader.isNotBlank()) {
            httpFactory.setDefaultRequestProperties(mapOf("Cookie" to cookieHeader))
        }
        return DefaultDataSource.Factory(context, httpFactory).createDataSource()
    }
}
