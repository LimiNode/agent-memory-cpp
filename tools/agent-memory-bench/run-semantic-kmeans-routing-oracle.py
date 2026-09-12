#!/usr/bin/env python3
"""Evaluate a full-dimensional semantic K-means posting oracle."""
from __future__ import annotations
import argparse, hashlib, json, time
from pathlib import Path
import numpy as np
from sklearn.cluster import MiniBatchKMeans

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--thq-manifest', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--sample-size', type=int, default=100_000)
    ap.add_argument('--query-limit', type=int, default=152)
    ap.add_argument('--seed', type=int, default=20260912)
    ap.add_argument('--batch-size', type=int, default=4096)
    ap.add_argument('--max-iter', type=int, default=100)
    ap.add_argument('--clusters', default='64,128,256,512')
    ap.add_argument('--replications', default='1,2,4')
    ap.add_argument('--nprobe', default='1,2,4,8,16,32')
    args = ap.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding='utf-8'))
    n, dim = int(manifest['documents']), int(manifest['dimension'])
    qn = min(args.query_limit, int(manifest['queries']))
    documents = np.memmap(manifest['references']['document_vectors']['path'], mode='r', dtype='<f4', shape=(n, dim))
    queries = np.memmap(manifest['references']['queries']['path'], mode='r', dtype='<f4', shape=(qn, dim))
    teachers = np.memmap(manifest['references']['teacher_ids']['path'], mode='r', dtype='<i8', shape=(qn, 10))
    clusters = [int(x) for x in args.clusters.split(',')]
    replications = [int(x) for x in args.replications.split(',')]
    nprobes = [int(x) for x in args.nprobe.split(',')]
    rng = np.random.default_rng(args.seed)
    sample_ids = np.sort(rng.choice(n, size=min(args.sample_size, n), replace=False))
    sample = np.asarray(documents[sample_ids], dtype=np.float32)
    rows = []
    models = []
    for k in clusters:
        if k <= 0 or k > n: raise ValueError('cluster count out of range')
        started = time.perf_counter()
        model = MiniBatchKMeans(n_clusters=k, random_state=args.seed, batch_size=args.batch_size,
                                max_iter=args.max_iter, n_init=1, reassignment_ratio=0.0,
                                init_size=max(k * 3, args.batch_size))
        model.fit(sample)
        centers = np.asarray(model.cluster_centers_, dtype=np.float32)
        assign_started = time.perf_counter()
        top = np.empty((n, max(replications)), dtype=np.int32)
        for lo in range(0, n, args.batch_size):
            hi = min(lo + args.batch_size, n)
            scores = np.asarray(documents[lo:hi], dtype=np.float32) @ centers.T
            rmax = top.shape[1]
            ids = np.argpartition(-scores, kth=rmax - 1, axis=1)[:, :rmax]
            vals = np.take_along_axis(scores, ids, axis=1)
            order = np.argsort(-vals, axis=1, kind='stable')
            top[lo:hi] = np.take_along_axis(ids, order, axis=1)
        assign_ms = (time.perf_counter() - assign_started) * 1000.0
        for r in replications:
            postings = [np.flatnonzero(np.any(top[:, :r] == c, axis=1)) for c in range(k)]
            for qi in range(qn):
                scores = centers @ np.asarray(queries[qi])
                order = np.lexsort((np.arange(k, dtype=np.int32), -scores))
                for p in nprobes:
                    probe = order[:min(p, k)]
                    pieces = [postings[int(c)] for c in probe]
                    union = np.unique(np.concatenate(pieces)) if pieces else np.empty(0, dtype=np.int64)
                    touched = int(sum(len(x) for x in pieces))
                    rows.append({'k': k, 'replication': r, 'query': qi, 'nprobe': p,
                                 'postings_touched': int(len(probe)),
                                 'posting_entries_touched': touched,
                                 'candidate_docs': int(len(union)),
                                 'replication_footprint': float(touched / n),
                                 'teacher_recall': float(np.isin(teachers[qi], union).sum() / 10.0)})
        models.append({'k': k, 'training_ms': (time.perf_counter() - started) * 1000.0,
                       'assignment_ms': assign_ms, 'center_sha256': hashlib.sha256(centers.tobytes()).hexdigest()})
    out = {'schema_version': 1, 'family': 'semantic_kmeans_routing_oracle_v1',
           'fixture_manifest_sha256': sha256(args.thq_manifest), 'runner_sha256': sha256(Path(__file__)),
           'documents': n, 'dimension': dim, 'queries': qn, 'sample_size': len(sample_ids),
           'sample_ids_sha256': hashlib.sha256(sample_ids.astype('<i8').tobytes()).hexdigest(),
           'seed': args.seed, 'clusters': clusters, 'replications': replications, 'nprobe': nprobes,
           'models': models, 'rows': rows,
           'protocol': {'assignment': 'full-dimensional cosine dot-product top-r nearest centroids',
                        'posting_union': 'deduplicated document IDs',
                        'teacher_ids_used_for_index': False, 'payload_rerank': 'not executed',
                        'production_activation': False},
           'execution_status': 'EXECUTED_SMOKE' if qn < 152 else 'EXECUTED',
           'production_activation': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, sort_keys=True) + '\n', encoding='utf-8')

if __name__ == '__main__':
    main()
