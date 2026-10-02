#include <bits/stdc++.h>
using namespace std;
bool number(istream& in, long long& x) {
    string token;
    if (!(in >> token)) return false;
    try {
        size_t used;
        x = stoll(token, &used);
        return used == token.size();
    } catch (...) { return false; }
}
int main(int argc, char** argv) {
    if (argc != 4) return 3;
    ifstream input(argv[1]), output(argv[2]);
    int t, n, a;
    if (!(input >> t)) return 3;
    while (t--) {
        if (!(input >> n)) return 3;
        long long sum = 0;
        for (int i = 0; i < n; ++i) {
            if (!(input >> a)) return 3;
            sum += a;
        }
        long long x, y;
        if (!number(output, x) || !number(output, y)) return 1;
        if (x < -1000000000000LL || x > 1000000000000LL ||
            y < -1000000000000LL || y > 1000000000000LL) return 1;
        if ((__int128)x + y != sum) return 1;
    }
    string extra;
    return output >> extra ? 1 : 0;
}
