#!/usr/bin/env node
/**
 * Sui transaction signer for Aftermath proxy.
 *
 * Signs either an unsponsored TransactionKind or a sponsored full
 * Transaction returned by the Aftermath V2 API, then submits to Sui.
 *
 * Usage:
 *   echo '{"txKind":"...","privateKey":"suiprivkey1...","rpcUrl":"..."}' | node _af_node_signer.mjs --stdin
 *   echo '{"privateKey":"suiprivkey1..."}' | node _af_node_signer.mjs --address
 *   node _af_node_signer.mjs <input.json>     (legacy file mode — deprecated)
 *
 * --stdin mode: reads JSON from stdin (no tmpfile, secure)
 * --address mode: derives wallet address from private key, outputs {"address":"0x..."}
 * file mode: reads JSON from file path argument (legacy, insecure — writes key to disk)
 *
 * Requires (install once in the agent-cli dir):
 *   npm install @mysten/sui
 */

import { readFileSync } from "node:fs";
import { SuiClient } from "@mysten/sui/client";
import { Ed25519Keypair } from "@mysten/sui/keypairs/ed25519";
import { Transaction } from "@mysten/sui/transactions";
import { fromBase64 } from "@mysten/sui/utils";
import { assertSafeSponsoredTransaction } from "./_af_sponsor_guard.mjs";

// Parse mode from args
const args = process.argv.slice(2);
const mode = args.includes("--address")
  ? "address"
  : args.includes("--stdin")
    ? "stdin"
    : "file";

async function readInput() {
  if (mode === "file") {
    const inputPath = args.find((a) => !a.startsWith("--"));
    if (!inputPath) {
      process.stderr.write(
        "Usage: node _af_node_signer.mjs --stdin | --address | <input.json>\n"
      );
      process.exit(1);
    }
    return JSON.parse(readFileSync(inputPath, "utf8"));
  }
  // Read from stdin
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

try {
  const input = await readInput();

  if (mode === "address") {
    // Derive wallet address only
    const { privateKey } = input;
    if (!privateKey) {
      process.stderr.write("Input must contain privateKey\n");
      process.exit(1);
    }
    const keypair = Ed25519Keypair.fromSecretKey(privateKey);
    const address = keypair.getPublicKey().toSuiAddress();
    process.stdout.write(JSON.stringify({ address }) + "\n");
  } else {
    // Sign and submit transaction
    const {
      txKind,
      privateKey,
      rpcUrl,
      sponsorSignature,
      maxGasBudget,
      maxGasPrice,
      allowedPackages,
    } = input;

    if (!txKind || !privateKey || !rpcUrl) {
      process.stderr.write(
        "Input must contain txKind, privateKey, and rpcUrl\n"
      );
      process.exit(1);
    }

    const keypair = Ed25519Keypair.fromSecretKey(privateKey);
    const client = new SuiClient({ url: rpcUrl });

    const txBytes = fromBase64(txKind);
    if (!(txBytes instanceof Uint8Array)) {
      throw new Error("txKind must decode to transaction bytes");
    }
    let result;

    if (sponsorSignature) {
      // Sponsored responses contain full TransactionData with gas payment
      // already attached. Preserve those bytes, sign them as the sender, and
      // submit both sender and sponsor signatures.
      const senderAddress = keypair.getPublicKey().toSuiAddress();
      assertSafeSponsoredTransaction({
        txBytes,
        senderAddress,
        maxGasBudget,
        maxGasPrice,
        allowedPackages,
      });

      // The sponsor signed these exact bytes. Rebuilding could change gas
      // fields or object versions and invalidate the sponsor signature.
      const { signature } = await keypair.signTransaction(txBytes);
      result = await client.executeTransactionBlock({
        transactionBlock: txBytes,
        signature: [signature, sponsorSignature],
        options: { showEffects: true },
      });
    } else {
      const tx = Transaction.fromKind(txBytes);
      result = await client.signAndExecuteTransaction({
        transaction: tx,
        signer: keypair,
        options: { showEffects: true },
      });
    }

    const status = result.effects?.status?.status;
    if (status !== "success") {
      const err = result.effects?.status?.error ?? "unknown";
      process.stderr.write(
        `Transaction ${result.digest ?? "unknown"} failed on-chain: ${err}\n`
      );
      process.exit(1);
    }

    // Wait for finality
    await client.waitForTransaction({ digest: result.digest });

    process.stdout.write(JSON.stringify({ digest: result.digest }) + "\n");
  }
} catch (e) {
  process.stderr.write(`Signer error: ${e.message ?? e}\n`);
  process.exit(1);
}
