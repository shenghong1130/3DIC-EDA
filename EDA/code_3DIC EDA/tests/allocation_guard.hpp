#pragma once
// Test executable only. Exercise production undo under actual heap failure,
// rather than merely throwing bad_alloc while the heap is still available.
#include <cstdlib>
#include <new>
bool fail_test_heap_allocations=false;
#if defined(__GNUC__)
#define GR_TEST_NOINLINE __attribute__((noinline))
#elif defined(_MSC_VER)
#define GR_TEST_NOINLINE __declspec(noinline)
#else
#define GR_TEST_NOINLINE
#endif
GR_TEST_NOINLINE void* operator new(std::size_t n){
    if(fail_test_heap_allocations)throw std::bad_alloc();
    if(auto*p=std::malloc(n?n:1))return p;
    throw std::bad_alloc();
}
GR_TEST_NOINLINE void* operator new[](std::size_t n){return ::operator new(n);}
GR_TEST_NOINLINE void operator delete(void*p)noexcept{std::free(p);}
GR_TEST_NOINLINE void operator delete[](void*p)noexcept{std::free(p);}
GR_TEST_NOINLINE void operator delete(void*p,std::size_t)noexcept{std::free(p);}
GR_TEST_NOINLINE void operator delete[](void*p,std::size_t)noexcept{std::free(p);}
#undef GR_TEST_NOINLINE
