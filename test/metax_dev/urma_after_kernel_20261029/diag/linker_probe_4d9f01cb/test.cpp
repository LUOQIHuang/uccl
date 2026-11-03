#include <string>
extern "C" unsigned long string_len(const char* p) { return std::string(p).size(); }
