#pragma once
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>
class VerbsWriteSession {
 public:
  VerbsWriteSession(std::string const& hca,int gid,size_t threads,size_t max_bytes,bool notifications=false);
  ~VerbsWriteSession();
  std::string descriptor(size_t) const;
  std::string metadata() const;
  void connect(std::vector<std::string> const&);
  void prepare(size_t bytes,size_t src,size_t dst,int rank);
  void experiment(bool send,bool receive,std::vector<int> const&);
  void arm(size_t sequence,int operation,bool visible);
  std::string step(size_t sequence);
  void validate(size_t sequence);
  void close();
 private:
  struct Impl;std::unique_ptr<Impl> impl_;
};
