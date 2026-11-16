"""One fixed, inactive Normal tokens sweep shared by URMA and verbs."""
TOKENS = (64, 128, 256, 512, 1024, 2048, 4096)
WARMUPS, SAMPLES, REPEATS = 10, 31, 3
VERSION = 1
DEPENDENCIES = (
    'test/verbs_baseline/driver.py', 'test/verbs_baseline/worker.py', 'test/verbs_baseline/common.py',
    'test/urma_mode_parity/driver.py', 'test/urma_mode_parity/worker.py', 'test/urma_mode_parity/support.py',
    'test/urma_mode_parity/phase_support.py', 'test/urma_mode_parity/phase_runtime.py',
    'test/reference_events/event_runtime.py', 'test/verbs_diag/profile_support.py',
    'ep/bench/test_internode.py', 'ep/bench/buffer.py', 'ep/bench/utils.py')
KERNELS = {'verbs': '6.6.0-145.3.34.165.oe2403sp3.aarch64',
           'urma': '6.6.0-gdr_f00955680_diag'}


def order(repeat):
    if repeat == 1:
        return TOKENS
    if repeat == 2:
        return tuple(reversed(TOKENS))
    if repeat == 3:
        return TOKENS[3:] + TOKENS[:3]
    raise ValueError('repeat must be 1, 2 or 3')


def cases(job, backend):
    return [dict(repeat=r, tokens=t, job='%s_%s_r%02d_t%d' % (job, backend, r, t))
            for r in range(1, REPEATS + 1) for t in order(r)]


def recipe(tokens):
    return dict(tokens=tokens, warmups=WARMUPS, samples=SAMPLES)
