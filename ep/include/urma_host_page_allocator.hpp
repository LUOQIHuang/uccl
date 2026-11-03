#pragma once
#include <cstddef>
#include <cstdlib>
#include <limits>
#include <new>
#include <unistd.h>

// The tested private UMDK requires page-aligned host segment addresses.
// Own whole pages so FADD writeback and barrier mappings never share a page
// with unrelated heap storage. Register the logical buffer length.
template<class T> class UrmaHostPageAllocator {
 public:
  using value_type=T;
  UrmaHostPageAllocator() noexcept=default;
  template<class U> UrmaHostPageAllocator(UrmaHostPageAllocator<U> const&) noexcept {}
  T* allocate(size_t count) {
    constexpr size_t maximum=std::numeric_limits<size_t>::max();
    if(count>maximum/sizeof(T)) throw std::bad_array_new_length();
    size_t bytes=count ? count*sizeof(T) : 1;
    long configured=sysconf(_SC_PAGESIZE);
    if(configured<=0) throw std::bad_alloc();
    size_t page=static_cast<size_t>(configured);
    if((page&(page-1)) || page<sizeof(void*) || page<alignof(T) || bytes>maximum-(page-1))
      throw std::bad_alloc();
    void* memory=nullptr;
    if(posix_memalign(&memory,page,(bytes+page-1)&~(page-1))) throw std::bad_alloc();
    return static_cast<T*>(memory);
  }
  void deallocate(T* pointer,size_t) noexcept { std::free(pointer); }
};
template<class T,class U>
bool operator==(UrmaHostPageAllocator<T> const&,UrmaHostPageAllocator<U> const&) noexcept { return true; }
template<class T,class U>
bool operator!=(UrmaHostPageAllocator<T> const&,UrmaHostPageAllocator<U> const&) noexcept { return false; }
