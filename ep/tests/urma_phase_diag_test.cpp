#include "urma_phase_diag.hpp"
#include <cassert>
#include <iostream>
int main() {
  using namespace uccl::urma_diag;
  count(Write,99);
  assert(records.empty());
  enabled=true;
  std::vector<std::thread> workers;
  for (int i=0;i<4;++i) workers.emplace_back([i] {
    identity(1,i);
    for (int j=0;j<1000;++j) { Scope timer(SdkPost,2); count(Write,64); }
    for (int j=0;j<128;++j) { auto s=sample(); complete(s); }
    for (int j=0;j<64;++j) {
      auto write=sample(WriteToObservedCqe); complete(write);
      auto fadd=sample(FaddToObservedCqe); complete(fadd);
    }
  });
  // Read while workers update: the API must not reset/free live counters.
  for (int i=0;i<20;++i) assert(snapshot().find("\"version\":1")!=std::string::npos);
  for (auto& w:workers) w.join();
  enabled=false;
  assert(records.size()==4);
  for (auto const& r:records) {
    assert(r->metrics[Write].calls==1000 && r->metrics[Write].items==64000);
    assert(r->metrics[SdkPost].calls==1000 && r->metrics[SdkPost].items==2000);
    assert(r->metrics[SubmitToObservedCqe].calls==4);
    assert(r->metrics[WriteToObservedCqe].calls==1 && r->metrics[FaddToObservedCqe].calls==1);
    count(Write,99); // Disabled instrumentation must leave counters unchanged.
  }
  std::cout << snapshot() << '\n';
}
