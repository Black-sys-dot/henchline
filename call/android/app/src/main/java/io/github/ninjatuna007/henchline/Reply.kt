package io.github.ninjatuna007.henchline

import android.net.Uri
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * How a ring went, told to the laptop: rang, declined, missed, unreachable. Each ring carries its own
 * one-time ntfy address for this (`handsfree ring` listens there), so it works off Tailscale as well,
 * and an agent can tell an ignored call from one that never reached the phone.
 */
object Reply {
    private val client = OkHttpClient.Builder().callTimeout(10, TimeUnit.SECONDS).build()

    /** Only an https address on the distributor's server: the ring's own, not anyone's URL. */
    fun valid(url: String?): String? = url?.takeIf {
        Uri.parse(it).let { u ->
            u.scheme == "https" && u.host == "ntfy.sh" && u.path?.startsWith("/henchline-") == true  // laptop-made
        }
    }

    fun send(url: String?, what: String) {
        val to = valid(url) ?: return
        Thread {
            try {
                client.newCall(Request.Builder().url(to).post(what.toRequestBody("text/plain".toMediaType())).build())
                    .execute().close()
            } catch (_: Exception) {  // offline: the laptop then hears nothing, which it reads as such
            }
        }.start()
    }
}
