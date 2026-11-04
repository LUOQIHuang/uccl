#pragma once

#include "urma_transport.hpp"
#include "common.hpp"

#ifdef USE_URMA
// Local registration only: no peer, MCCL, WR submission, or host fallback.
// Keep every transport alive to reproduce endpoint/resource pressure and
// verify that the shared main/control ranges are registered only once.
inline void probe_urma_registration(void* main, size_t bytes, void* control,
                                    size_t control_bytes, int device,
                                    bool normal, size_t channels, size_t count) {
  if (count < 1 || count > 4) throw std::invalid_argument("probe count must be 1..4");
  std::vector<ProxyCtx> contexts(count);
  std::vector<std::unique_ptr<UrmaTransport>> transports;
  for (size_t i = 0; i < count; ++i) {
    contexts[i].local_rank = device;
    contexts[i].thread_idx = i;
    auto transport = std::make_unique<UrmaTransport>();
    transport->configure(normal, channels);
    transport->init(contexts[i]);
    transport->register_main_buffer(main, bytes, device);
    transport->register_atomic_buffer(control, control_bytes, device);
    transports.push_back(std::move(transport));
  }
}
#endif
