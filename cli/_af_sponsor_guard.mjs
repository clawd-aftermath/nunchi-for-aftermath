/**
 * Validate remote sponsored TransactionData before the local key signs it.
 *
 * The Aftermath API is an untrusted transaction builder. These checks bind
 * the transaction to the expected sender and cap sponsor-controlled gas.
 */

import { Transaction } from "@mysten/sui/transactions";
import {
  normalizeSuiAddress,
  normalizeSuiObjectId,
} from "@mysten/sui/utils";

function positiveBigInt(value, label) {
  let parsed;
  try {
    parsed = BigInt(value);
  } catch {
    throw new Error(`${label} must be a positive integer`);
  }
  if (parsed <= 0n) {
    throw new Error(`${label} must be a positive integer`);
  }
  return parsed;
}

function normalizedAddress(value, label) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} is required`);
  }
  try {
    return normalizeSuiAddress(value);
  } catch {
    throw new Error(`${label} must be a valid Sui address`);
  }
}

function normalizedAllowedPackages(allowedPackages) {
  if (!Array.isArray(allowedPackages) || allowedPackages.length === 0) {
    throw new Error(
      "AF_ALLOWED_PACKAGES must explicitly allow sponsored Move packages"
    );
  }
  return new Set(
    allowedPackages.map((packageId) => {
      try {
        return normalizeSuiObjectId(packageId);
      } catch {
        throw new Error(
          `AF_ALLOWED_PACKAGES contains invalid package ID ${packageId}`
        );
      }
    })
  );
}

export function assertSafeSponsoredCommands(commands, allowedPackages) {
  const allowed = normalizedAllowedPackages(allowedPackages);
  if (!Array.isArray(commands)) {
    throw new Error(
      "Sponsored transaction must contain a ProgrammableTransaction command list"
    );
  }
  const permittedNonCallKinds = new Set([
    "SplitCoins",
    "MergeCoins",
    "MakeMoveVec",
  ]);
  let moveCallCount = 0;

  for (const command of commands) {
    const kind = command?.$kind;
    if (kind === "Publish" || kind === "Upgrade" || kind === "TransferObjects") {
      throw new Error(`Sponsored transaction contains forbidden ${kind} command`);
    }
    if (permittedNonCallKinds.has(kind)) {
      continue;
    }
    if (kind !== "MoveCall") {
      throw new Error(
        `Sponsored transaction contains unsupported ${kind ?? "unknown"} command`
      );
    }

    moveCallCount += 1;
    const packageId = normalizeSuiObjectId(command.MoveCall.package);
    if (!allowed.has(packageId)) {
      throw new Error(
        `Sponsored MoveCall package ${packageId} is not in AF_ALLOWED_PACKAGES`
      );
    }
  }

  if (moveCallCount === 0) {
    throw new Error("Sponsored transaction must contain an allowed MoveCall");
  }
}

export function assertSafeSponsoredTransaction({
  txBytes,
  senderAddress,
  maxGasBudget,
  maxGasPrice,
  allowedPackages,
}) {
  if (!(txBytes instanceof Uint8Array)) {
    throw new Error("Sponsored txKind must be exact transaction bytes");
  }
  const txData = Transaction.from(txBytes).getData();
  const gasData = txData?.gasData;
  if (
    !txData?.sender ||
    !gasData?.owner ||
    gasData?.budget == null ||
    gasData?.price == null ||
    !gasData?.payment?.length
  ) {
    throw new Error(
      "Sponsored txKind must contain fully resolved TransactionData"
    );
  }

  const expectedSender = normalizedAddress(senderAddress, "Signing key address");
  const transactionSender = normalizedAddress(
    txData.sender,
    "Transaction sender"
  );
  const gasOwner = normalizedAddress(gasData.owner, "Transaction gas owner");

  if (transactionSender !== expectedSender) {
    throw new Error(
      `Sponsored transaction sender ${txData.sender} does not match signing key ${senderAddress}`
    );
  }
  if (gasOwner === transactionSender) {
    throw new Error(
      "Sponsored transaction gas owner must differ from transaction sender"
    );
  }

  const budget = positiveBigInt(gasData.budget, "Sponsored gas budget");
  const price = positiveBigInt(gasData.price, "Sponsored gas price");
  const budgetCeiling = positiveBigInt(
    maxGasBudget,
    "Sponsored gas budget ceiling"
  );
  const priceCeiling = positiveBigInt(
    maxGasPrice,
    "Sponsored gas price ceiling"
  );

  if (budget > budgetCeiling) {
    throw new Error(
      `Sponsored gas budget ${budget} exceeds configured ceiling ${budgetCeiling}`
    );
  }
  if (price > priceCeiling) {
    throw new Error(
      `Sponsored gas price ${price} exceeds configured ceiling ${priceCeiling}`
    );
  }

  assertSafeSponsoredCommands(txData.commands, allowedPackages);
  return txData;
}
