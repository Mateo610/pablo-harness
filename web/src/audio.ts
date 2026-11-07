// Browser audio for voice calls: record the mic as 16 kHz mono WAV, play replies in order,
// and expose analysers so the orb can follow the real sound.

export interface Recorder {
  stop(): Blob;
  cancel(): void;
  /** Forget all but the last `seconds`: an open mic keeps only what came just before you spoke. */
  keepLast(seconds: number): void;
  analyser: AnalyserNode;
  level(): number;
}

let shared: AudioContext | null = null;
export function audioContext(): AudioContext {
  if (!shared) shared = new AudioContext();
  if (shared.state === "suspended") void shared.resume();
  return shared;
}

let micStream: MediaStream | null = null;
export async function microphone(): Promise<MediaStream> {
  if (micStream && micStream.getAudioTracks().some((t) => t.readyState === "live")) return micStream;
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  return micStream;
}

export function releaseMicrophone(): void {
  micStream?.getTracks().forEach((t) => t.stop());
  micStream = null;
}

/** Start recording now. `stop()` returns the take as a WAV blob. */
export async function record(): Promise<Recorder> {
  const ctx = audioContext();
  const source = ctx.createMediaStreamSource(await microphone());
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.6;
  // ScriptProcessor is old but works everywhere without a separate worklet file.
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  proc.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  const mute = ctx.createGain();
  mute.gain.value = 0;
  source.connect(analyser);
  source.connect(proc);
  proc.connect(mute);
  mute.connect(ctx.destination);
  const buf = new Uint8Array(analyser.fftSize);
  const end = () => {
    proc.disconnect();
    source.disconnect();
    mute.disconnect();
  };
  return {
    analyser,
    level: () => rms(analyser, buf),
    cancel: end,
    keepLast: (seconds) => {
      let keep = Math.ceil((seconds * ctx.sampleRate) / 4096);
      if (chunks.length > keep) chunks.splice(0, chunks.length - (keep = Math.max(1, keep)));
    },
    stop: () => {
      end();
      return wav(downsample(concat(chunks), ctx.sampleRate, 16000), 16000);
    },
  };
}

export function rms(analyser: AnalyserNode, buf: Uint8Array): number {
  analyser.getByteTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = (buf[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / buf.length);
}

function concat(chunks: Float32Array[]): Float32Array {
  const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.length;
  }
  return out;
}

function downsample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return input;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio);
    const stop = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < stop; j++) sum += input[j];
    out[i] = sum / Math.max(1, stop - start);
  }
  return out;
}

function wav(samples: Float32Array, rate: number): Blob {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (at: number, s: string) => [...s].forEach((ch, i) => view.setUint8(at + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s)) * 0x7fff, true));
  return new Blob([view], { type: "audio/wav" });
}

/**
 * The clip without the silence the voice server pads it with at both ends (keeps `pad` seconds).
 * Back to back, that padding was most of the gap between sentences.
 */
export function trimSilence(ctx: BaseAudioContext, buffer: AudioBuffer, threshold = 0.01, pad = 0.06): AudioBuffer {
  const data = buffer.getChannelData(0);
  let first = 0;
  while (first < data.length && Math.abs(data[first]) < threshold) first++;
  let last = data.length - 1;
  while (last > first && Math.abs(data[last]) < threshold) last--;
  if (first >= last) return buffer; // all quiet: leave it as it came
  const padding = Math.round(pad * buffer.sampleRate);
  const from = Math.max(0, first - padding);
  const to = Math.min(data.length, last + 1 + padding);
  if (from === 0 && to === data.length) return buffer;
  const out = ctx.createBuffer(buffer.numberOfChannels, to - from, buffer.sampleRate);
  for (let c = 0; c < buffer.numberOfChannels; c++) out.copyToChannel(buffer.getChannelData(c).subarray(from, to), c);
  return out;
}

/** Plays spoken sentences back to back, in the order they were sent. */
export class Player {
  readonly analyser: AnalyserNode;
  private next = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private waiting = new Map<number, ArrayBuffer>();
  private expected = 0;
  private pending = 0; // clips taken in and not yet playing (being decoded)
  private dropping = false; // stopped by barge-in: the rest of this reply is not wanted
  private gen = 0; // which reply: a clip still decoding from an older one never plays
  private chain: Promise<void> = Promise.resolve();
  private levelBuf = new Uint8Array(256);
  /** When the clip now sounding started (AudioContext time), for the open mic's grace period. */
  clipStartedAt = 0;
  onIdle: () => void = () => undefined;
  onError: (message: string) => void = () => undefined;

  constructor() {
    const ctx = audioContext();
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.7;
    this.analyser.connect(ctx.destination);
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  /** Playing, or about to: a clip is decoding or waiting for its turn. Until this is false the
   * reply isn't over, so nothing else may start talking over it or reset it. */
  get busy(): boolean {
    return this.sources.size > 0 || this.pending > 0 || this.waiting.size > 0;
  }

  /** How loud Pablo is right now (what the mic may be hearing back). */
  level(): number {
    return this.sources.size > 0 ? rms(this.analyser, this.levelBuf) : 0;
  }

  /** Base64 audio for sentence `seq`. Out-of-order arrivals wait for their turn. */
  push(seq: number, base64: string): void {
    if (this.dropping) return;
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    this.waiting.set(seq, bytes.buffer);
    while (this.waiting.has(this.expected)) {
      const data = this.waiting.get(this.expected)!;
      this.waiting.delete(this.expected++);
      this.pending++;
      const gen = this.gen;
      this.chain = this.chain
        .then(() => this.play(data, gen))
        .catch((e: Error) => this.onError(`Couldn't play Pablo's voice: ${e.message || e.name}`))
        .finally(() => {
          this.pending--;
          if (!this.busy) this.onIdle();
        });
    }
  }

  private async play(data: ArrayBuffer, gen: number): Promise<void> {
    const ctx = audioContext();
    const buffer = trimSilence(ctx, await ctx.decodeAudioData(data));
    if (this.dropping || gen !== this.gen) return;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.analyser);
    const at = Math.max(ctx.currentTime + 0.02, this.next);
    src.start(at);
    this.next = at + buffer.duration;
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      if (!this.busy) this.onIdle();
    };
    if (this.sources.size === 1) this.clipStartedAt = at;
  }

  /** Barge-in: you started talking, so it stops, and the rest of this reply is dropped. */
  stop(): void {
    this.dropping = true;
    this.silence();
  }

  /** A new reply: forget the last one and take clips again. */
  reset(): void {
    this.silence();
    this.dropping = false;
  }

  private silence(): void {
    this.gen++;
    this.sources.forEach((s) => {
      s.onended = null;
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    });
    this.sources.clear();
    this.waiting.clear();
    this.expected = 0;
    this.next = 0;
    this.chain = Promise.resolve();
  }
}
