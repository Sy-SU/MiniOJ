#include <iostream>
int main() {
    int t, n, a;
    if (!(std::cin >> t)) return 1;
    while (t--) {
        std::cin >> n;
        long long sum = 0;
        for (int i = 0; i < n; ++i) { std::cin >> a; sum += a; }
        std::cout << sum << '\n';
    }
}
