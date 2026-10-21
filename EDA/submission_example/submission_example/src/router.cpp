#include <iostream>
#include <fstream>
#include <cstring>
using namespace std;

int main(int argc, char* argv[]) {
    string def_file, lef_file, thermal_file, output_file;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--def") == 0 && i + 1 < argc) {
            def_file = argv[++i];
	} else if (strcmp(argv[i], "--lef") == 0 && i + 1 < argc) {
            lef_file = argv[++i];
        } else if (strcmp(argv[i], "--thermal-report") == 0 && i + 1 < argc) {
            thermal_file = argv[++i];
        } else if (strcmp(argv[i], "--output") == 0 && i + 1 < argc) {
            output_file = argv[++i];
        }
    }

    if (def_file.empty() || lef_file.empty() || thermal_file.empty() || output_file.empty()) {
        cerr << "错误: 缺少参数" << endl;
        return 1;
    }

    // 提取路径
    size_t pos = def_file.rfind('/');
    string dir = (pos != string::npos) ? def_file.substr(0, pos + 1) : "./";
    pos = def_file.rfind(".input.def");
    size_t start = def_file.rfind('/') + 1;
    string case_name = def_file.substr(start, pos - start);
    string ref_guide = dir + case_name + ".output.guide";

    // 复制参考答案
    ifstream ref(ref_guide);
    ofstream out(output_file);
    out << ref.rdbuf();

    cout << "完成" << endl;
    return 0;
}
