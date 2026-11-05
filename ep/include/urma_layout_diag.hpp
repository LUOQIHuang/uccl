#pragma once
// Diagnostic only: capture actual Normal WRITE geometry in a separate run.
#include <atomic>
#include <cstdint>
#include <map>
#include <mutex>
#include <sstream>
#include <tuple>
namespace uccl::urma_layout {
inline std::atomic<bool> enabled{false};
inline std::mutex mutex;
using Key=std::tuple<uint32_t,uint32_t,uint32_t,uint32_t>;
inline std::map<Key,uint64_t> counts;
inline uint64_t dropped=0;
inline void begin() {
  std::lock_guard<std::mutex> lock(mutex);
  counts.clear();dropped=0;enabled=true;
}
inline void record(uint32_t bytes,uint64_t src,uint64_t dst,size_t lane) {
  if (!enabled.load(std::memory_order_relaxed)) return;
  std::lock_guard<std::mutex> lock(mutex);
  if (!enabled.load(std::memory_order_relaxed)) return;
  Key key{bytes,uint32_t(src%4096),uint32_t(dst%4096),uint32_t(lane)};
  if (counts.size()>=8192 && !counts.count(key)) {++dropped;return;}
  ++counts[key];
}
inline std::string end() {
  std::lock_guard<std::mutex> lock(mutex);
  enabled=false;std::ostringstream out;
  out<<"{\"version\":1,\"modulus\":4096,\"dropped\":"<<dropped<<",\"writes\":[";
  bool first=true;
  for(auto const& item:counts) {
    if(!first)out<<',';
    first=false;
    out<<"{\"bytes\":"<<std::get<0>(item.first)<<",\"src_mod\":"<<std::get<1>(item.first)
       <<",\"dst_mod\":"<<std::get<2>(item.first)<<",\"lane\":"<<std::get<3>(item.first)
       <<",\"count\":"<<item.second<<'}';
  }
  out<<"]}";return out.str();
}
}
