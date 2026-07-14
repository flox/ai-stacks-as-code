# Ask-Flox — cited retrieval over the Flox docs + blog, served as an MCP tool.
#
# DATA + CODE package (no executable): Flox wraps any packaged binary into the
# build-env closure, which lacks the pkg-grouped Python deps — so the CONSUMING
# env provides python + mcp + chromadb/onnxruntime (no torch) and runs
# share/ask-flox/pipeline/mcp_server.py. See envs/ask-flox-demo.
#
# Reproducibility strategy:
#   * The Chroma index is NON-reproducible (random collection/segment UUIDs +
#     multi-threaded HNSW), so it is built ONCE and committed as index.tar.gz,
#     pinned by content. Regenerate it (`just index`) and re-tar when the corpus
#     changes — see scripts/refresh-ask-flox-index.sh.
#   * The ONNX embedding model IS reproducible and is fetched as a fixed-output
#     derivation (its upstream SHA is chroma's own _MODEL_SHA256).
{ runCommand, fetchurl }:

let
  onnxModel = fetchurl {
    url = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz";
    sha256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3";
  };
in
runCommand "ask-flox-0.1.0"
  {
    meta.description =
      "Ask Flox: cited retrieval over the Flox docs + blog, served as an MCP tool";
  }
  ''
    dst=$out/share/ask-flox
    mkdir -p "$dst/onnx-model/all-MiniLM-L6-v2"

    # Server + retrieval code (drop __pycache__).
    cp -r ${../../../pipeline} "$dst/pipeline"
    chmod -R u+w "$dst/pipeline"
    find "$dst/pipeline" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

    # Prebuilt, content-pinned index (extracts to index/ + index-manifest.json).
    tar -xzf ${./index.tar.gz} -C "$dst"

    # ONNX embedding model (extracts to onnx/).
    tar -xzf ${onnxModel} -C "$dst/onnx-model/all-MiniLM-L6-v2"

    # Launch logic shipped as data; the consuming env puts it on PATH.
    cp ${../../../packaging/ask-flox-mcp} "$dst/ask-flox-mcp"
  ''
