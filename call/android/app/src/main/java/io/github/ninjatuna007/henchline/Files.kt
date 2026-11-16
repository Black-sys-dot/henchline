package io.github.ninjatuna007.henchline

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Files the agents send (`handsfree send` on the laptop): shown in the call's chat, or as a notification off a
 * call. Opening one saves it to Downloads/Henchline, so it stays on the phone, and hands it to whatever app
 * opens that type. Pictures are previewed in the chat.
 */
object Files {
    const val CHANNEL = "files"
    const val URL = "file_url"
    const val NAME = "file_name"
    const val TYPE = "file_type"
    const val SIZE = "file_size"

    val client: OkHttpClient = OkHttpClient.Builder().readTimeout(60, TimeUnit.SECONDS).build()

    /** The laptop's address as Henchline knows it, e.g. https://laptop.tailnet.ts.net */
    fun origin(context: Context): String? {
        val server = context.getSharedPreferences("call", Context.MODE_PRIVATE).getString("server", "") ?: ""
        val uri = Uri.parse(server)
        return if (uri.scheme == "https" && !uri.authority.isNullOrEmpty()) "https://${uri.authority}" else null
    }

    /** A link from the laptop: /files/… from the call, or a whole address from a push. Only the laptop's own
     *  address is followed, so a push can't point the phone anywhere else. */
    fun resolve(context: Context, url: String): String? {
        val origin = origin(context) ?: return null
        return when {
            url.startsWith("/files/") -> origin + url
            url.startsWith("$origin/files/") -> url
            else -> null
        }
    }

    /** Saved to Downloads/Henchline, then opened; `note` hears how it went (on the main thread). */
    fun open(context: Context, url: String, name: String, type: String, note: (String) -> Unit) {
        val address = resolve(context, url) ?: return note("That file isn't from your laptop's address, so I didn't open it.")
        val main = android.os.Handler(context.mainLooper)
        note("Downloading $name…")
        Thread {
            val saved = try {
                download(context, address, name, type)
            } catch (e: Exception) {
                main.post { note("Couldn't download $name: ${e.message ?: "no connection"} (is Tailscale on?)") }
                return@Thread
            }
            main.post {
                try {
                    context.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(saved, type)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
                    note("Saved to Downloads/Henchline: $name")
                } catch (_: ActivityNotFoundException) {
                    note("Saved to Downloads/Henchline: $name (no app here opens $type)")
                }
            }
        }.start()
    }

    private fun download(context: Context, url: String, name: String, type: String): Uri {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, type)
            put(MediaStore.Downloads.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/Henchline")
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw IllegalStateException("no room in Downloads")
        try {
            client.newCall(Request.Builder().url(url).build()).execute().use { answer ->
                if (!answer.isSuccessful) throw IllegalStateException(
                    if (answer.code == 404) "the laptop no longer has it (kept a week)" else "the laptop said ${answer.code}")
                resolver.openOutputStream(uri)!!.use { out -> answer.body!!.byteStream().copyTo(out) }
            }
        } catch (e: Exception) {
            resolver.delete(uri, null, null)
            throw e
        }
        resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
        return uri
    }

    /** A small picture for the chat, or null; kept in the cache, so a screen rebuilt doesn't fetch it again. */
    fun preview(context: Context, url: String, widthPx: Int): Bitmap? {
        val address = resolve(context, url) ?: return null
        val cached = File(context.cacheDir, "previews/" + address.hashCode().toUInt().toString(16))
        try {
            if (!cached.exists()) {
                cached.parentFile?.mkdirs()
                client.newCall(Request.Builder().url(address).build()).execute().use { answer ->
                    if (!answer.isSuccessful) return null
                    val part = File(cached.path + "." + java.util.UUID.randomUUID() + ".part")  // two fetches never share one
                    part.outputStream().use { out -> answer.body!!.byteStream().copyTo(out) }
                    part.renameTo(cached)
                }
            }
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(cached.path, bounds)
            var sample = 1
            while (bounds.outWidth / (sample * 2) >= widthPx) sample *= 2
            // Not only by width: a tall page (1920 × 20000) would decode to 150 MB, more than a screen can draw
            while (bounds.outWidth / sample > 4096 || bounds.outHeight / sample > 4096 ||
                   (bounds.outWidth.toLong() / sample) * (bounds.outHeight.toLong() / sample) > 16_000_000L) sample *= 2
            return BitmapFactory.decodeFile(cached.path, BitmapFactory.Options().apply { inSampleSize = sample })
        } catch (_: Exception) {
            return null
        } catch (_: OutOfMemoryError) {  // a picture too big even so: not shown, never a crash
            return null
        }
    }

    /** An agent sent a file while no call was up, or while the call screen wasn't showing. */
    fun notify(context: Context, agent: String, words: String, url: String, name: String, type: String, size: Long) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Files from your crew", NotificationManager.IMPORTANCE_HIGH))
        val open = Intent(context, MainActivity::class.java).setAction(MainActivity.OPEN_FILE)
            .putExtra(URL, url).putExtra(NAME, name).putExtra(TYPE, type).putExtra(SIZE, size)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val id = url.hashCode()
        manager.notify(id, Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("${IncomingActivity.nameOf(agent)} sent you $name")
            .setContentText(words.ifEmpty { "Tap to open it." })
            .setStyle(Notification.BigTextStyle().bigText(words.ifEmpty { "Tap to open it." }))
            .setContentIntent(PendingIntent.getActivity(context, id, open,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .setAutoCancel(true)
            .build())
    }

    /** A small copy of a picture you send (shared or attached), kept with the chats: the chat shows it later, when
     *  the app that lent the picture may have taken it back. The path, or null for anything but a picture. */
    fun keepThumb(context: Context, uri: Uri): String? {
        if (context.contentResolver.getType(uri)?.startsWith("image/") != true) return null
        return try {
            val picture = context.contentResolver.loadThumbnail(uri, android.util.Size(640, 640), null)
            val out = File(context.filesDir, "chats/thumbs/" + java.util.UUID.randomUUID() + ".jpg")
            out.parentFile?.mkdirs()
            out.outputStream().use { picture.compress(Bitmap.CompressFormat.JPEG, 85, it) }
            out.path
        } catch (_: Exception) {
            null
        }
    }

    /** A shared or picked file's name, as the app it came from gives it. */
    fun nameOf(context: Context, uri: Uri): String = try {
        context.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) it.getString(0) else null
        }
    } catch (_: Exception) {
        null
    } ?: uri.lastPathSegment?.substringAfterLast('/') ?: "file"

    fun sizeText(size: Long): String = when {
        size < 1024 -> "$size bytes"
        size < 1024 * 1024 -> "%.1f KB".format(size / 1024.0)
        size < 1024L * 1024 * 1024 -> "%.1f MB".format(size / 1024.0 / 1024)
        else -> "%.1f GB".format(size / 1024.0 / 1024 / 1024)
    }
}
