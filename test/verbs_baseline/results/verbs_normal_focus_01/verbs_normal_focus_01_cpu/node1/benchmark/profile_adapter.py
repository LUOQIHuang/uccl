def bench_kineto(
    fn,
    kernel_names: Union[str, tuple],
    num_tests: int = 30,
    suppress_kineto_output: bool = False,
    trace_path: Optional[str] = None,
    barrier_comm_profiling: bool = False,
    num_kernels_per_period: int = 1,
):
    # Profile
    suppress = suppress_stdout_stderr if suppress_kineto_output else empty_suppress
    with suppress():
        schedule = torch.profiler.schedule(wait=0, warmup=1, active=1, repeat=1)
        with torch.profiler.profile(
            activities=[torch.profiler.ProfilerActivity.CUDA], schedule=schedule
        ) as prof:
            for i in range(2):
                # NOTES: use a large kernel and a barrier to eliminate the unbalanced CPU launch overhead
                if barrier_comm_profiling:
                    current_device = torch.cuda.current_device()
                    lhs = torch.randn(
                        (8192, 8192),
                        dtype=torch.float,
                        device=f"cuda:{current_device}",
                    )
                    rhs = torch.randn(
                        (8192, 8192),
                        dtype=torch.float,
                        device=f"cuda:{current_device}",
                    )
                    lhs @ rhs
                    dist.all_reduce(
                        torch.ones(
                            1,
                            dtype=torch.float,
                            device=f"cuda:{current_device}",
                        )
                    )
                for _ in range(num_tests):
                    fn()
                torch.cuda.synchronize()
                dist.barrier()
                prof.step()

    # Parse the profiling table
    assert isinstance(kernel_names, str) or isinstance(kernel_names, tuple)
    is_tuple = isinstance(kernel_names, tuple)
    prof_lines = (
        prof.key_averages()
        .table(sort_by="cuda_time_total", max_name_column_width=100)
        .split("\n")
    )
    kernel_names = (kernel_names,) if isinstance(kernel_names, str) else kernel_names
    assert all([isinstance(name, str) for name in kernel_names])
    for name in kernel_names:
        count = sum([name in line for line in prof_lines])
        if count != 1:
            print(f"\n[WARNING] Profiling table for kernel '{name}':")
            print("\n".join(prof_lines))
            print(
                f"[WARNING] Kernel '{name}' found {count} times in profiling table (expected 1)"
            )
            print(f"[WARNING] Continuing execution despite mismatch...\n")

    # Save chrome traces
    if trace_path is not None:
        prof.export_chrome_trace(trace_path)

    # Return average kernel durations
    units = {"ms": 1e3, "us": 1e6}
    kernel_durations = []
    for name in kernel_names:
        found = False
        for line in prof_lines:
            if name in line:
                time_str = line.split()[-2]
                for unit, scale in units.items():
                    if unit in time_str:
                        kernel_durations.append(
                            float(time_str.replace(unit, "")) / scale
                        )
                        found = True
                        break
                break
        # NOTE(MaoZiming): in rare cases it misses certain events.
        if not found:
            print(
                f"[WARNING] Kernel '{name}' not found in profiling table, using 0.0 as placeholder"
            )
            kernel_durations.append(0.0)

    # Expand the kernels by periods
    if num_kernels_per_period > 1:
        if trace_path is not None:
            # Kineto traces may only be exported once. Reuse the saved trace
            # when also extracting LL send/recv periods.
            profile_data = json.loads(Path(trace_path).read_text())
        else:
            with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
                prof.export_chrome_trace(tmp.name)
                profile_data = json.loads(Path(tmp.name).read_text())

        for i, kernel_name in enumerate(kernel_names):
            events = [
                event
                for event in profile_data["traceEvents"]
                if f"::{kernel_name}" in event["name"]
            ]
            events = sorted(events, key=lambda event: event["ts"])
            durations = [event["dur"] / 1e6 for event in events]

            # Handle incomplete periods gracefully (due to dropped samples)
            # NOTE(MaoZiming): This sometimes happen, suspect it is the profiler's issue.
            num_complete_periods = len(durations) // num_kernels_per_period
            if len(durations) % num_kernels_per_period != 0:
                dropped_samples = len(durations) % num_kernels_per_period
                if dist.get_rank() == 0:
                    print(
                        f"[WARNING] Kernel '{kernel_name}': {dropped_samples} samples dropped "
                        f"(got {len(durations)} samples, expected multiple of {num_kernels_per_period}). "
                        f"Using {num_complete_periods} complete periods.",
                        flush=True,
                    )
                # Truncate to only use complete periods
                durations = durations[: num_complete_periods * num_kernels_per_period]
            if num_complete_periods > 0:
                kernel_durations[i] = [
                    sum(durations[j::num_kernels_per_period]) / num_complete_periods
                    for j in range(num_kernels_per_period)
                ]
            else:
                # If we have no complete periods, fall back to returning zeros
                if dist.get_rank() == 0:
                    print(
                        f"[WARNING] Kernel '{kernel_name}': No complete periods found. "
                        f"Returning zeros.",
                        flush=True,
                    )
                kernel_durations[i] = [0.0] * num_kernels_per_period

    # Return execution durations
    return kernel_durations if is_tuple else kernel_durations[0]
