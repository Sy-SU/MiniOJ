#include <iostream>
#include <random>
#include <string>
int main(int argc, char** argv) {
    if (argc != 3) return 1;
    int id = std::stoi(argv[1]);
    std::mt19937_64 rng(std::stoull(argv[2]) + id);
    int n = id == 1 ? 1 : id == 2 ? 2 : id >= 9 ? 1000 : id * 7;
    std::cout << 1 << '\n' << n << '\n';
    for (int i = 0; i < n; ++i) {
        int a = id == 1 ? -100 : id == 2 ? 100 : id == 4 ? 0 : id == 6 ? (i % 2 ? -100 : 100) : int(rng() % 201) - 100;
        std::cout << a << (i + 1 == n ? '\n' : ' ');
    }
}
