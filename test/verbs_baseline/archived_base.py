"""Reuse only builds with the exact recorded, GPU-trace-off compiled inputs."""
import json
from pathlib import Path
import common
REVISION='664c8193134bbd360caea322e2ec6a0630bfd4d5'
REFERENCE_SHA='c9c647ff644983a0c3ba03b9bac95ce3cb46c1b01c6c3ef621bc1aabb00ae985'
RECORD=common.HERE/'results/verbs_stage_01/verbs_stage_01_baseline_r01/node0/module_manifest.json'
RECORD_DIGEST='d4216f573cba68dd746d20fac6137ed65d51fc691adf407054decee3f65e1a0f'
KERNEL='6.6.0-145.3.34.165.oe2403sp3.aarch64'

def load(build_dir):
    reference=json.loads(RECORD.read_text())
    if common.digest(reference)!=RECORD_DIGEST:raise RuntimeError('ARCHIVE_REFERENCE_CHANGED')
    manifest=json.loads((build_dir/'manifest.json').read_text())
    if any(manifest.get(k)!=reference[k] for k in ('revision','sources','flags')):
        raise RuntimeError('ARCHIVE_INPUTS_NOT_PINNED_BASE: requires the recorded 664c8193 base build')
    module=(build_dir/manifest['module']).resolve()
    try:module.relative_to(build_dir.resolve())
    except ValueError:raise RuntimeError('ARCHIVE_MODULE_OUTSIDE_BUILD')
    if not module.is_file() or common.sha(module)!=manifest['module_sha']:raise RuntimeError('ARCHIVE_MODULE_SHA_MISMATCH')
    identity=dict(kind='pinned_archived_source_build',revision=REVISION,sources_hash=common.digest(manifest['sources']),flags=manifest['flags'],reference_module_sha=REFERENCE_SHA,
                  note='Archived compiled inputs, frozen benchmark Python; current diagnostic workers. Binary SHA verified against local original manifest; runtime still requires correctness checks.')
    return module,manifest,identity
