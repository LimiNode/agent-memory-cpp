#!/usr/bin/env python3
"""Materialize full-corpus packed LSQ/PLSQ payloads from frozen models."""
from __future__ import annotations
import argparse, hashlib, json, struct
from pathlib import Path
import numpy as np

D, N, THQ_BYTES = 384, 1_000_000, 96

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()

def unpack_thq(codes: np.ndarray) -> np.ndarray:
    out = np.empty((len(codes), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = codes[:, b]
        out[:, 4*b:4*b+4] = np.stack((x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out

def make_lsq(model: dict, width: int):
    import faiss
    q = faiss.LocalSearchQuantizer(D, width, 8)
    faiss.copy_array_to_vector(np.asarray(model[f'lsq{width}_codebooks'], dtype=np.float32).ravel(), q.codebooks)
    faiss.copy_array_to_vector(np.asarray(model[f'lsq{width}_offsets'], dtype=np.uint64), q.codebook_offsets)
    q.is_trained = True; q.nperts = 1; q.encode_ils_iters = 1; q.icm_iters = 1
    return q

def make_plsq(model: dict, profile: str):
    import faiss
    splits, msub = (8, 4) if profile in ('8x4', '8x4x8') else (8, 6)
    q = faiss.ProductLocalSearchQuantizer(D, splits, msub, 8)
    for split in range(splits):
        local = faiss.downcast_AdditiveQuantizer(q.subquantizer(split))
        faiss.copy_array_to_vector(np.asarray(model[f'split_{split}_codebooks'], dtype=np.float32), local.codebooks)
        faiss.copy_array_to_vector(np.asarray(model[f'split_{split}_offsets'], dtype=np.uint64), local.codebook_offsets)
        local.is_trained = True; local.nperts = 1; local.encode_ils_iters = 1; local.icm_iters = 1
    q.is_trained = True
    return q

def write_lsq_payload(path: Path, ids: np.ndarray, codes: np.ndarray,
                      norms: np.ndarray, centroids: np.ndarray,
                      books: np.ndarray) -> None:
    """Write the AMLSQ01 layout consumed by the native full-flat scorer."""
    stages = int(codes.shape[1])
    dimensions = int(centroids.shape[0])
    with path.open('wb') as f:
        f.write(b'AMLSQ01\0')
        f.write(struct.pack('<III', stages, dimensions, int(codes.shape[0])))
        f.write(np.asarray(ids, dtype='<i4').tobytes())
        f.write(np.asarray(codes, dtype=np.uint8).tobytes())
        f.write(np.asarray(books, dtype='<f4').reshape(stages, 256, dimensions).tobytes())
        f.write(np.asarray(centroids, dtype='<f4').reshape(4 * dimensions).tobytes())
        f.write(np.asarray(norms, dtype='<f4').tobytes())

def write_plsq_payload(path: Path, codes: np.ndarray, norms: np.ndarray,
                       centroids: np.ndarray, books: list[np.ndarray],
                       splits: int, sub: int) -> None:
    """Write the AMPLSQF1 full-corpus layout for the native PLSQ scorer."""
    with path.open('wb') as f:
        f.write(b'AMPLSQF1')
        f.write(struct.pack('<IIII', int(codes.shape[0]), splits, sub,
                            int(codes.shape[1])))
        f.write(np.asarray(codes, dtype=np.uint8).tobytes())
        f.write(np.asarray(norms, dtype='<f4').tobytes())
        f.write(np.asarray(centroids, dtype='<f4').reshape(4 * D).tobytes())
        for book in books:
            f.write(np.asarray(book, dtype='<f4').tobytes())

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--documents', type=Path, required=True); p.add_argument('--thq', type=Path, required=True)
    p.add_argument('--models', type=Path, required=True); p.add_argument('--plsq-model', type=Path); p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--chunk', type=int, default=4096); p.add_argument('--profiles', default='lsq32,lsq48,plsq8x6')
    p.add_argument('--limit', type=int, default=N, help='prefix rows for a bounded smoke run (default: full corpus)')
    a = p.parse_args(); a.output_dir.mkdir(parents=True, exist_ok=True)
    if a.documents.stat().st_size != N * D * 4 or a.thq.stat().st_size != N * THQ_BYTES: raise RuntimeError('source shape differs')
    limit = min(max(1, a.limit), N)
    docs = np.memmap(a.documents, mode='r', dtype='<f4', shape=(N, D)); thq = np.memmap(a.thq, mode='r', dtype=np.uint8, shape=(N, THQ_BYTES))
    with np.load(a.models, allow_pickle=False) as z: model = {k: np.asarray(z[k]) for k in z.files}
    plsq_model = model
    if a.plsq_model:
        with np.load(a.plsq_model, allow_pickle=False) as z: plsq_model = {k: np.asarray(z[k]) for k in z.files}
    centroids = np.asarray(model['centroids'], dtype=np.float32); ids = np.arange(N, dtype=np.int32)
    import faiss
    outputs = {}
    for profile in [x.strip() for x in a.profiles.split(',') if x.strip()]:
        source_model = model if profile.startswith('lsq') else plsq_model
        profile_centroids = np.asarray(source_model['centroids'], dtype=np.float32)
        if profile.startswith('lsq'):
            width = int(profile[3:]); q = make_lsq(source_model, width); code_width = width; books = [source_model[f'lsq{width}_codebooks']]
        elif profile.startswith('plsq'):
            q = make_plsq(source_model, profile[4:]); code_width = q.code_size; books = [source_model[f'split_{s}_codebooks'] for s in range(8)]
        else: raise RuntimeError(f'unsupported profile {profile}')
        codes_path = a.output_dir / f'{profile}.codes.u8'; norms_path = a.output_dir / f'{profile}.norms.f32'; codes_path.unlink(missing_ok=True); norms_path.unlink(missing_ok=True)
        with codes_path.open('wb') as cf, norms_path.open('wb') as nf:
            for start in range(0, limit, a.chunk):
                stop = min(limit, start + a.chunk); levels = unpack_thq(np.asarray(thq[start:stop])); base = profile_centroids[np.arange(D)[None, :], levels]; residual = np.ascontiguousarray(np.asarray(docs[start:stop], dtype=np.float32) - base)
                codes = np.asarray(q.compute_codes(residual), dtype=np.uint8)
                if profile.startswith('plsq'):
                    # Faiss ProductAdditiveQuantizer::decode is not safe when
                    # fed a manually frozen model on all supported wheels;
                    # reconstruct the additive residual directly from the
                    # immutable codebooks instead.
                    splits, sub = 8, (4 if profile == 'plsq8x4' else 6)
                    split_dim = D // splits
                    decoded = np.zeros_like(residual, dtype=np.float32)
                    for split in range(splits):
                        for part in range(sub):
                            symbols = codes[:, split * sub + part]
                            book = np.asarray(source_model[f'split_{split}_codebooks'], dtype=np.float32).reshape(sub, 256, split_dim)[part]
                            decoded[:, split * split_dim:(split + 1) * split_dim] += book[symbols]
                else:
                    decoded = np.asarray(q.decode(codes), dtype=np.float32)
                norms = np.linalg.norm(np.asarray(base + decoded, dtype=np.float64), axis=1).astype('<f4')
                cf.write(codes.tobytes()); nf.write(norms.tobytes())
        payload_path = a.output_dir / f'{profile}.payload.bin'
        if profile.startswith('lsq'):
            # Re-open the raw sidecars as compact arrays; this avoids retaining
            # the entire corpus in Python while keeping the payload ordering
            # identical to the generated code stream.
            all_codes = np.memmap(codes_path, mode='r', dtype=np.uint8,
                                  shape=(limit, code_width))
            all_norms = np.memmap(norms_path, mode='r', dtype='<f4', shape=(limit,))
            write_lsq_payload(payload_path, ids[:limit], all_codes, all_norms,
                              profile_centroids, np.asarray(books[0], dtype=np.float32))
        else:
            profile_tail = profile[4:]
            splits, sub = (8, 4) if profile_tail in ('8x4', '8x4x8') else (8, 6)
            all_codes = np.memmap(codes_path, mode='r', dtype=np.uint8,
                                  shape=(limit, code_width))
            all_norms = np.memmap(norms_path, mode='r', dtype='<f4', shape=(limit,))
            write_plsq_payload(payload_path, all_codes, all_norms, profile_centroids,
                               [np.asarray(book, dtype=np.float32) for book in books],
                               splits, sub)
        outputs[profile] = {'codes': str(codes_path), 'norms': str(norms_path),
                            'payload': str(payload_path) if payload_path.exists() else None,
                            'code_bytes': code_width,
                            'codes_sha256': sha(codes_path),
                            'norms_sha256': sha(norms_path),
                            'payload_sha256': sha(payload_path) if payload_path.exists() else None}
    result = {'schema_version': 1, 'family': 'native_full_flat_finalist_materialization_v1', 'status': 'EXECUTED', 'documents': limit, 'source_documents': N, 'dimension': D, 'source_hashes': {'documents': sha(a.documents), 'thq': sha(a.thq), 'models': sha(a.models), **({'plsq_model': sha(a.plsq_model)} if a.plsq_model else {})}, 'profiles': outputs, 'runner_sha256': sha(Path(__file__))}
    (a.output_dir / 'materialization.result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(result, sort_keys=True))

if __name__ == '__main__': main()
