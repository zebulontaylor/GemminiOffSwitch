// Reuse the compiled model while starting each grant case from time zero.
#include "Vcycle_tb.h"
#include "verilated.h"

#include <chrono>
#include <cstdio>
#include <fstream>
#include <string>
#include <unistd.h>

static int run_case(int cycles, const std::string& workload, const std::string& lock_bit) {
    VerilatedContext context;
    context.threads(1);
    context.fatalOnError(false);
    const std::string cycle_arg = "+CYCLES=" + std::to_string(cycles);
    const std::string workload_arg = "+WORKLOAD=" + workload;
    const std::string lock_arg = "+LOCK_BIT=" + lock_bit;
    const char* args[] = {"off_switch_lock", cycle_arg.c_str(), workload_arg.c_str(), lock_arg.c_str()};
    context.commandArgs(4, args);
    Vcycle_tb model(&context);
    while (!context.gotFinish()) {
        model.eval();
        if (!context.gotFinish()) {
            if (!model.eventsPending()) return 1;
            context.time(model.nextTimeSlot());
        }
    }
    model.final();
    return context.gotError() ? 1 : 0;
}

int main(int argc, char** argv) {
    if (argc < 4 || argc % 2 != 0) return 2;
    const int cycles = std::stoi(argv[1]);
    const int console = dup(STDOUT_FILENO);
    if (console == -1) return 2;
    int status = 0;
    for (int arg = 2; arg < argc; arg += 2) {
        const std::string name = std::string("workload-") + argv[arg] + "-lock-" + argv[arg + 1];
        FILE* log = std::fopen((name + ".log").c_str(), "w");
        if (!log) return 2;
        std::fflush(stdout);
        if (dup2(fileno(log), STDOUT_FILENO) == -1) return 2;
        std::fclose(log);
        const auto start = std::chrono::steady_clock::now();
        const int result = run_case(cycles, argv[arg], argv[arg + 1]);
        const std::chrono::duration<double> elapsed = std::chrono::steady_clock::now() - start;
        std::printf("Verilator case seconds=%.6f\n", elapsed.count());
        std::fflush(stdout);
        if (dup2(console, STDOUT_FILENO) == -1) return 2;
        std::ofstream exit_code(name + ".exitcode");
        exit_code << result << '\n';
        if (!exit_code) return 2;
        status |= result;
    }
    close(console);
    return status;
}
