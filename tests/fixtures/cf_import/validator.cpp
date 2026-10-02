#include <iostream>
int main() {
    long long t, n, a, total = 0;
    if (!(std::cin >> t) || t < 1 || t > 5) return 1;
    while (t--) {
        if (!(std::cin >> n) || n < 1 || n > 1000) return 2;
        total += n;
        for (int i = 0; i < n; ++i) {
            if (!(std::cin >> a) || a < -100 || a > 100) return 3;
        }
    }
    if (total > 2000) return 4;
    std::string extra;
    return std::cin >> extra ? 5 : 0;
}
