import assert from "node:assert/strict";
import test from "node:test";

import { Transaction } from "@mysten/sui/transactions";

import {
  assertSafeSponsoredCommands,
  assertSafeSponsoredTransaction,
} from "./_af_sponsor_guard.mjs";

const SENDER = `0x${"1".repeat(64)}`;
const SPONSOR = `0x${"2".repeat(64)}`;
const PACKAGE = `0x${"4".repeat(64)}`;

async function transactionBytes(overrides = {}) {
  const tx = new Transaction();
  tx.setSender(overrides.sender ?? SENDER);
  tx.setGasOwner(overrides.gasOwner ?? SPONSOR);
  tx.setGasPrice(overrides.gasPrice ?? 1000);
  tx.setGasBudget(overrides.gasBudget ?? 50_000_000);
  tx.setGasPayment([
    {
      objectId: `0x${"3".repeat(64)}`,
      version: "1",
      digest: "11111111111111111111111111111111",
    },
  ]);
  if (overrides.withSplitCoins) {
    tx.splitCoins(tx.gas, [tx.pure.u64(1)]);
  }
  tx.moveCall({
    target: `${PACKAGE}::perpetuals::place_order`,
    arguments: [],
  });
  return tx.build();
}

async function validate(overrides = {}, limits = {}) {
  return assertSafeSponsoredTransaction({
    txBytes: await transactionBytes(overrides),
    senderAddress: SENDER,
    maxGasBudget: "100000000",
    maxGasPrice: "10000",
    allowedPackages: [PACKAGE],
    ...limits,
  });
}

test("accepts exact resolved bytes within local policy", async () => {
  const txData = await validate();

  assert.equal(txData.sender, SENDER);
  assert.equal(txData.commands[0].$kind, "MoveCall");
});

test("accepts a real SplitCoins plus allowed MoveCall command sequence", async () => {
  const txData = await validate({ withSplitCoins: true });

  assert.deepEqual(
    txData.commands.map((command) => command.$kind),
    ["SplitCoins", "MoveCall"]
  );
});

test("rejects a sender that does not match the signing key", async () => {
  await assert.rejects(
    () => validate({ sender: `0x${"5".repeat(64)}` }),
    /does not match signing key/
  );
});

test("rejects gas owned by the transaction sender", async () => {
  await assert.rejects(
    () => validate({ gasOwner: SENDER }),
    /gas owner must differ/
  );
});

test("rejects sponsored gas budget above configured ceiling", async () => {
  await assert.rejects(
    () => validate({ gasBudget: 100_000_001 }),
    /gas budget .* exceeds configured ceiling/
  );
});

test("rejects sponsored gas price above configured ceiling", async () => {
  await assert.rejects(
    () => validate({ gasPrice: 10_001 }),
    /gas price .* exceeds configured ceiling/
  );
});

test("rejects absent or nonpositive gas ceilings", async () => {
  await assert.rejects(
    () => validate({}, { maxGasBudget: undefined }),
    /gas budget ceiling must be a positive integer/
  );
  await assert.rejects(
    () => validate({}, { maxGasPrice: "0" }),
    /gas price ceiling must be a positive integer/
  );
});

test("rejects untrusted MoveCall package", async () => {
  await assert.rejects(
    () => validate({}, { allowedPackages: [`0x${"6".repeat(64)}`] }),
    /is not in AF_ALLOWED_PACKAGES/
  );
});

test("rejects missing package allowlist", async () => {
  await assert.rejects(
    () => validate({}, { allowedPackages: undefined }),
    /AF_ALLOWED_PACKAGES must explicitly allow/
  );
});

test("rejects forbidden Publish, Upgrade, and TransferObjects commands", () => {
  for (const kind of ["Publish", "Upgrade", "TransferObjects"]) {
    assert.throws(
      () =>
        assertSafeSponsoredCommands(
          [{ $kind: kind, [kind]: {} }],
          [PACKAGE]
        ),
      new RegExp(`forbidden ${kind}`)
    );
  }
});

test("rejects unknown or unlisted command kinds", () => {
  assert.throws(
    () =>
      assertSafeSponsoredCommands(
        [{ $kind: "FutureDangerousCommand", FutureDangerousCommand: {} }],
        [PACKAGE]
      ),
    /unsupported FutureDangerousCommand/
  );
  assert.throws(
    () => assertSafeSponsoredCommands([{}], [PACKAGE]),
    /unsupported unknown/
  );
});

test("rejects malformed MoveCall commands", () => {
  assert.throws(
    () => assertSafeSponsoredCommands([{ $kind: "MoveCall" }], [PACKAGE])
  );
});

test("rejects a transaction without a programmable command list", () => {
  assert.throws(
    () => assertSafeSponsoredCommands(undefined, [PACKAGE]),
    /must contain a ProgrammableTransaction command list/
  );
});
