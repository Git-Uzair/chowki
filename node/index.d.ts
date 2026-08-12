/**
 * Placeholder types for the planned chowki Node/TypeScript SDK.
 * The implementation is roadmap phase 3; the Python SDK ships today.
 */

/** Always `"planned"` until the SDK lands. */
export declare const status: "planned";

/** URL of the shipped Python package. */
export declare const pythonPackage: string;

/** Repository URL. */
export declare const repository: string;

/** Throws — the Node SDK is not implemented yet. */
export declare function createEngine(): never;

declare const _default: {
  status: typeof status;
  pythonPackage: typeof pythonPackage;
  repository: typeof repository;
  createEngine: typeof createEngine;
};
export default _default;
