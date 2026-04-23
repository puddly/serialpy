import { test, expect } from "bun:test";
import { createFakeSerialPair } from "./fake_serial";

async function openBoth() {
  const [a, b] = createFakeSerialPair();
  await a.open({ baudRate: 115200 });
  await b.open({ baudRate: 115200 });
  return [a, b] as const;
}

test("bytes flow left -> right", async () => {
  const [a, b] = await openBoth();
  const writer = a.writable!.getWriter();
  const reader = b.readable!.getReader();

  await writer.write(new Uint8Array([1, 2, 3, 4]));
  const { value, done } = await reader.read();
  expect(done).toBe(false);
  expect(Array.from(value!)).toEqual([1, 2, 3, 4]);

  writer.releaseLock();
  reader.releaseLock();
  await a.close();
});

test("bytes flow right -> left", async () => {
  const [a, b] = await openBoth();
  const writer = b.writable!.getWriter();
  const reader = a.readable!.getReader();

  await writer.write(new Uint8Array([9, 8, 7]));
  const { value } = await reader.read();
  expect(Array.from(value!)).toEqual([9, 8, 7]);

  writer.releaseLock();
  reader.releaseLock();
  await a.close();
});

test("RTS on one side mirrors to CTS on the other", async () => {
  const [a, b] = await openBoth();
  await a.setSignals({ requestToSend: true });
  expect((await b.getSignals()).clearToSend).toBe(true);
  await a.setSignals({ requestToSend: false });
  expect((await b.getSignals()).clearToSend).toBe(false);
  await a.close();
});

test("DTR mirrors to DSR and DCD on the peer", async () => {
  const [a, b] = await openBoth();
  await a.setSignals({ dataTerminalReady: true });
  const s = await b.getSignals();
  expect(s.dataSetReady).toBe(true);
  expect(s.dataCarrierDetect).toBe(true);
  expect(s.ringIndicator).toBe(false);
  await a.close();
});

test("closing one side ends its own reader but leaves the peer intact", async () => {
  const [a, b] = await openBoth();
  const ownReader = a.readable!.getReader();
  await a.close();
  const { done } = await ownReader.read();
  expect(done).toBe(true);

  // Peer still sees writes the peer makes to itself - but any writes from A
  // are dropped silently (A.writable is gone).
  const peerWriter = b.writable!.getWriter();
  const peerReader = b.readable!.getReader();
  await peerWriter.write(new Uint8Array([42]));
  // No peer data comes back since A is closed.
  // Verify peer can still close cleanly.
  peerWriter.releaseLock();
  peerReader.releaseLock();
  await b.close();
});
