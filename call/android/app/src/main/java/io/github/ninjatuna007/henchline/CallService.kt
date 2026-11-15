package io.github.ninjatuna007.henchline

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.LinkedBlockingDeque
import java.util.concurrent.TimeUnit

/**
 * The call: the phone's mic to the call server, the agents' voices back, as the web page does,
 * but in a foreground service, so it keeps going with the screen off or the app in the background.
 *
 * The server's protocol is the page's: binary frames are audio (16 kHz s16 out, 24 kHz s16 in),
 * text frames JSON. The phone's own voice-call audio mode cancels its echo, as the browser did.
 */
class CallService : Service() {

    /** What the screen shows: the service is the call, the activity only watches it. */
    object Bus {
        @Volatile var listener: ((String) -> Unit)? = null  // JSON from the server, or {"closed": reason}
        @Volatile var connected = false
        @Volatile var service: CallService? = null  // set from Call until the call ends: also "connecting"
    }

    // Written on the main and OkHttp threads, read on the mic thread
    @Volatile private var socket: WebSocket? = null
    @Volatile private var recorder: Thread? = null
    @Volatile private var player: Thread? = null
    private var focus: AudioFocusRequest? = null
    // Clear voice (the default): the agents as media, not through the phone-call path, which sounded muffled and
    // tinny; the mic then asks for the phone's echo canceller itself. Tested 2026-11-15: a lot clearer, and no
    // echo of a 22 s clip came back. Off: the phone-call path, its routing and processing
    @Volatile var clear = true
        private set
    private val generation = java.util.concurrent.atomic.AtomicInteger()  // the audio threads' round
    private val switching = java.util.concurrent.atomic.AtomicBoolean()  // a sound switch under way: taps wait
    private val audioLock = Any()  // starting the audio and switching its sound take turns
    private val attributes get() = if (clear) MEDIA else VOICE
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var running = false
    @Volatile private var ended = false  // the screen was already told why the call ended
    private val heard = LinkedBlockingDeque<ByteArray>()  // the agents' voices, waiting to be played
    private val audio by lazy { getSystemService(AudioManager::class.java) }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        goForeground()  // first: a service started for the foreground must say so within seconds
        val server = intent?.getStringExtra(SERVER)
        if (server == null) {
            if (!running) stopSelf()
            return START_NOT_STICKY
        }
        if (running) return START_NOT_STICKY  // already calling or connecting
        clear = intent.getBooleanExtra(CLEAR, true)
        running = true
        Bus.service = this
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "henchline:call").apply { acquire() }
        applySound()
        connect(server)
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        running = false
        socket?.close(1000, "hung up")
        socket = null
        recorder?.interrupt()
        player?.interrupt()
        heard.clear()
        synchronized(audioLock) {  // after any sound switch under way, so it can't set call mode again after this
            focus?.let { audio.abandonAudioFocusRequest(it) }
            if (Build.VERSION.SDK_INT >= 31) audio.clearCommunicationDevice()
            audio.mode = AudioManager.MODE_NORMAL
        }
        wakeLock?.let { if (it.isHeld) it.release() }
        Bus.connected = false
        Bus.service = null
        if (!ended) Bus.listener?.invoke("""{"closed": "Call ended"}""")
        super.onDestroy()
    }

    fun send(json: String) {
        socket?.send(json)
    }

    /** Clear or call sound, mid-call: the audio starts again in the other mode, the connection stays. */
    fun switchSound(toClear: Boolean): Boolean {
        if (!switching.compareAndSet(false, true)) return false  // a second quick tap: the first is still switching
        Thread {
            try {
                synchronized(audioLock) {
                    val (mic, speaker) = recorder to player
                    clear = toClear
                    generation.incrementAndGet()  // the running threads see this and stop
                    mic?.join(1000)
                    speaker?.join(1000)
                    if (running) {  // the call may have ended meanwhile: leave the phone's audio as onDestroy set it
                        applySound()
                        startAudio()
                    }
                }
            } finally {
                switching.set(false)
            }
        }.start()
        return true
    }

    /** The phone's audio for this sound: call mode and routing, or plain media; focus to match. */
    private fun applySound() {
        focus?.let { audio.abandonAudioFocusRequest(it) }
        if (clear) {
            if (Build.VERSION.SDK_INT >= 31) audio.clearCommunicationDevice()
            audio.mode = AudioManager.MODE_NORMAL
        } else {
            audio.mode = AudioManager.MODE_IN_COMMUNICATION  // the phone's echo cancelling, and call routing
            route()
        }
        // Music and other sound pause or duck for the call, as for a phone call
        focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
            .setAudioAttributes(attributes).build().also { audio.requestAudioFocus(it) }
    }

    private fun connect(server: String) {
        val base = Uri.parse(server)
        if (base.scheme != "https") {  // Android refuses plain http, and the call page needs https for the mic
            fail("The address must start with https:// (the laptop's `tailscale serve` name). Long-press Call.")
            return
        }
        val origin = "${base.scheme}://${base.authority}"  // the server lets only its own page's origin in
        val url = origin.replaceFirst("http", "ws") + "/call"
        val client = OkHttpClient.Builder().pingInterval(15, TimeUnit.SECONDS).build()
        val request = Request.Builder().url(url).header("Origin", origin).build()
        socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Bus.connected = true
                startAudio()
                Bus.listener?.invoke("""{"opened": true}""")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                Bus.listener?.invoke(text)
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                heard.offer(bytes.toByteArray())
                while (heard.size > MAX_BEHIND) heard.poll()  // over a second behind: drop the oldest
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                fail(reason.ifEmpty { "Call ended" })  // "busy: another call is up", or the server stopping
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                // A self-signed certificate (call/server.py --host tailscale) fails here: the app trusts only
                // real ones, so it needs the `tailscale serve` address
                fail("Couldn't reach the call: ${t.message}")
            }
        })
    }

    private fun startAudio() = synchronized(audioLock) {
        val round = generation.get()
        // The server logs each sound, so turns can be compared by it (transcription, turn taking)
        socket?.send(org.json.JSONObject().put("sound", if (clear) "clear" else "call").toString())
        recorder = Thread {
            try {
                val size = maxOf(AudioRecord.getMinBufferSize(MIC_RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT), FRAME * 2 * 4)
                val mic = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, MIC_RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
                if (mic.state != AudioRecord.STATE_INITIALIZED) {
                    mic.release()
                    fail("The microphone isn't available (another app using it, or no permission).")
                    return@Thread
                }
                val buffer = ByteArray(FRAME * 2)
                val effects = if (clear) listOfNotNull(  // outside call mode, ask for them by name
                    if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(mic.audioSessionId) else null,
                    if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(mic.audioSessionId) else null,
                ).onEach { it.enabled = true } else emptyList()
                if (clear) Bus.listener?.invoke(org.json.JSONObject().put("note",
                    "Clear voice: phone echo canceller ${if (effects.any { it is AcousticEchoCanceler }) "on" else "not available"}").toString())
                try {
                    mic.startRecording()
                    while (running && generation.get() == round) {
                        val got = mic.read(buffer, 0, buffer.size)
                        if (got < 0) {
                            fail("The microphone stopped working (error $got).")
                            break
                        }
                        if (got > 0) socket?.send(buffer.toByteString(0, got))
                    }
                } finally {
                    if (mic.recordingState == AudioRecord.RECORDSTATE_RECORDING) mic.stop()
                    effects.forEach { it.release() }
                    mic.release()
                }
            } catch (e: Exception) {  // never let the call take the app down with it
                fail("The microphone failed: ${e.message}")
            }
        }.apply { name = "mic"; start() }
        player = Thread {
            try {
                val track = AudioTrack.Builder()
                    .setAudioAttributes(attributes)
                    .setAudioFormat(AudioFormat.Builder().setSampleRate(SPEAKER_RATE)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .setBufferSizeInBytes(SPEAKER_RATE / 5 * 2)
                    .build()
                try {
                    track.play()
                    while (running && generation.get() == round) {
                        val chunk = heard.poll(200, TimeUnit.MILLISECONDS) ?: continue
                        val written = track.write(chunk, 0, chunk.size)
                        if (written < 0) {
                            fail("The speaker stopped working (error $written).")
                            break
                        }
                    }
                } finally {
                    if (track.playState == AudioTrack.PLAYSTATE_PLAYING) track.stop()
                    track.release()
                }
            } catch (_: InterruptedException) {
            } catch (e: Exception) {
                fail("The speaker failed: ${e.message}")
            }
        }.apply { name = "speaker"; start() }
    }

    /** End the call with a reason the screen shows; safe from any thread. */
    private fun fail(why: String) {
        if (ended) return
        ended = true
        Bus.listener?.invoke(org.json.JSONObject().put("closed", why).toString())
        android.os.Handler(mainLooper).post { stopSelf() }
    }

    /** Earbuds or a headset when there are some, else the loudspeaker (a call would pick the earpiece). */
    private fun route() {
        if (Build.VERSION.SDK_INT < 31) {
            @Suppress("DEPRECATION")
            audio.isSpeakerphoneOn = true
            return
        }
        val devices = audio.availableCommunicationDevices
        val preferred = listOf(AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
            AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)
        preferred.firstNotNullOfOrNull { type -> devices.firstOrNull { it.type == type } }
            ?.let { audio.setCommunicationDevice(it) }
    }

    private fun goForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Call", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE)
        val hangUp = PendingIntent.getService(this, 1, Intent(this, CallService::class.java).setAction(STOP),
            PendingIntent.FLAG_IMMUTABLE)
        val notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Henchline: on a call with your crew")
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Hang up", hangUp).build())
            .build()
        startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
    }

    companion object {
        const val SERVER = "server"
        const val CLEAR = "clear"
        const val STOP = "stop"
        const val CHANNEL = "call"
        const val MIC_RATE = 16000  // what hands-free listens at
        const val SPEAKER_RATE = 24000  // Kokoro's own rate
        const val FRAME = MIC_RATE / 50  // 20 ms of samples per message, as the page sends
        const val MAX_BEHIND = 50  // chunks queued before the oldest are dropped: the server sends 20 ms, so a second
        val VOICE: AudioAttributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
        val MEDIA: AudioAttributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
    }
}
