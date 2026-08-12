// The Node/TypeScript SDK is not implemented yet. This package exists so the name
// resolves to the real project rather than to nothing, and so `npm view chowki` points
// at the repository and the shipped Python SDK.
//
// Progress: https://github.com/Git-Uzair/chowki  (roadmap phase 3)

export const status = "planned";

export const pythonPackage = "https://pypi.org/project/chowki/";

export const repository = "https://github.com/Git-Uzair/chowki";

/**
 * Throws. The Node SDK has no implementation yet; this is deliberate rather than a
 * missing export, so a mistaken install fails loudly instead of silently doing nothing.
 */
export function createEngine() {
  throw new Error(
    "chowki: the Node/TypeScript SDK is not implemented yet (roadmap phase 3). " +
      "The Python SDK is available today: pip install chowki — " +
      "https://github.com/Git-Uzair/chowki",
  );
}

export default { status, pythonPackage, repository, createEngine };
