// In-memory Web-Serial-shaped port pair for testing Pyodide JS interop

class FakeSerialPort {
  #peer = null;
  #open = false;
  #readable = null;
  #writable = null;
  #inboundController = null;
  #out = { requestToSend: false, dataTerminalReady: false, break: false };

  static link(a, b) {
    a.#peer = b;
    b.#peer = a;
  }

  get readable() {
    return this.#readable;
  }

  get writable() {
    return this.#writable;
  }

  async open(_options) {
    if (this.#open) {
      throw new Error("FakeSerialPort is already open");
    }
    this.#open = true;

    this.#readable = new ReadableStream({
      start: (controller) => {
        this.#inboundController = controller;
      },
    });

    this.#writable = new WritableStream({
      write: (chunk) => {
        const bytes = new Uint8Array(
          chunk.buffer ?? chunk,
          chunk.byteOffset ?? 0,
          chunk.byteLength ?? chunk.length ?? 0,
        ).slice();
        const peer = this.#peer;
        if (peer && peer.#inboundController) {
          peer.#inboundController.enqueue(bytes);
        }
      },
    });
  }

  async close() {
    if (!this.#open) return;
    this.#open = false;

    if (this.#inboundController) {
      try {
        this.#inboundController.close();
      } catch {}
      this.#inboundController = null;
    }

    // Cable-unplugged semantics: the peer sees done:true too.
    const peer = this.#peer;
    if (peer && peer.#inboundController) {
      try {
        peer.#inboundController.close();
      } catch {}
      peer.#inboundController = null;
    }

    this.#readable = null;
    this.#writable = null;
  }

  async setSignals(signals = {}) {
    if ("requestToSend" in signals) {
      this.#out.requestToSend = !!signals.requestToSend;
    }
    if ("dataTerminalReady" in signals) {
      this.#out.dataTerminalReady = !!signals.dataTerminalReady;
    }
    if ("break" in signals) {
      this.#out.break = !!signals.break;
    }
  }

  async getSignals() {
    const peerOut = this.#peer ? this.#peer.#out : {};
    return {
      clearToSend: !!peerOut.requestToSend,
      dataCarrierDetect: !!peerOut.dataTerminalReady,
      dataSetReady: !!peerOut.dataTerminalReady,
      ringIndicator: false,
    };
  }
}

export function createFakeSerialPair() {
  const left = new FakeSerialPort();
  const right = new FakeSerialPort();
  FakeSerialPort.link(left, right);
  return [left, right];
}

export { FakeSerialPort };
