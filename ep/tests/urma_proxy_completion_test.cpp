// Compile production Proxy poll/capacity methods with a CPU completion source.
#include "transport.hpp"
#include "urma_phase_diag.hpp"
#include <algorithm>
#include <cassert>
#include <chrono>
#include <deque>
#include <memory>
#include <stdexcept>
#include <unordered_set>
#include <vector>

constexpr uint64_t kUrmaInternalWrId=UINT64_MAX;
inline void cpu_relax() {}
struct UrmaTransport {
  std::deque<TransportCompletion> ready;
  int poll(TransportCompletion* out,int max) {
    int n=0;
    while(n<max && !ready.empty()) {out[n++]=ready.front();ready.pop_front();}
    return n;
  }
};
struct Proxy {
  size_t urma_outstanding_ops_=0;
  std::vector<std::shared_ptr<UrmaTransport>> urma_transports_by_rank_;
  std::unordered_set<uint64_t> acked_wrs_;
  struct {void update_timer(){}} adaptive_sleeper_;
  void poll_urma_completions();
  void ensure_urma_capacity(size_t);
};
#include "proxy_completion.inc"

int main() {
  for(unsigned retired:{1U,2U}) {
    Proxy p;auto t=std::make_shared<UrmaTransport>();p.urma_transports_by_rank_.push_back(t);
    p.urma_outstanding_ops_=retired;
    p.poll_urma_completions();
    assert(p.acked_wrs_.empty() && p.urma_outstanding_ops_==retired);
    TransportCompletion c;c.wr_id=7;c.retired_ops=retired;t->ready.push_back(c);
    p.ensure_urma_capacity(kMaxOutstandingSends); // quiet/barrier full drain
    assert(p.urma_outstanding_ops_==0 && p.acked_wrs_.count(7));
    p.acked_wrs_.clear();c.wr_id=kUrmaInternalWrId;t->ready.push_back(c);p.urma_outstanding_ops_=retired;
    p.poll_urma_completions();assert(p.acked_wrs_.empty() && p.urma_outstanding_ops_==0);
    if(retired==2) {
      c.wr_id=9;c.secondary_wr_id=8;c.has_secondary=true;t->ready.push_back(c);
      p.urma_outstanding_ops_=2;p.poll_urma_completions();
      assert(p.acked_wrs_.count(8) && p.acked_wrs_.count(9) && p.urma_outstanding_ops_==0);
    }
  }
  for(int fail:{0,1,2}) {
    Proxy p;auto t=std::make_shared<UrmaTransport>();p.urma_transports_by_rank_.push_back(t);
    TransportCompletion c;c.wr_id=8;c.retired_ops=fail ? 2 : 0;c.status=fail==1 ? 1 : 0;
    c.has_secondary=fail==2;c.secondary_wr_id=kUrmaInternalWrId;
    p.urma_outstanding_ops_=2;t->ready.push_back(c);
    bool threw=false;try{p.poll_urma_completions();}catch(std::runtime_error const&){threw=true;}
    assert(threw && p.acked_wrs_.empty());
  }
}
