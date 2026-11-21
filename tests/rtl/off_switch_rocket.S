    .equ STATUS, 0x00
    .equ SUBMIT, 0x08
    .equ NONCE, 0x20
    .equ SIGNATURE, 0x40
    .equ SITE_STRIDE, 0x80

    .equ READY, 1
    .equ BUSY, 2
    .equ SIGNER_ONE, 4
    .equ ENABLED, 8
    .equ DONE, 16
    .equ PASSED, 32

    .section .text
    .globl _start
_start:
    la t0, trap
    csrw mtvec, t0
    li s0, 0x10010000
    addi s1, s0, SITE_STRIDE

    li a0, 1
    li t1, READY
1:  lw t0, STATUS(s0)
    beqz t0, 1b
    bne t0, t1, report
2:  lw t0, STATUS(s1)
    beqz t0, 2b
    bne t0, t1, report

    li a0, 2
    ld s2, NONCE(s0)
    ld s3, NONCE+8(s0)
    ld s4, NONCE+16(s0)
    ld s5, NONCE+24(s0)
    la a1, initial_nonce
    ld a2, 0(a1)
    bne s2, a2, report
    ld a2, 8(a1)
    bne s3, a2, report
    ld a2, 16(a1)
    bne s4, a2, report
    ld a2, 24(a1)
    bne s5, a2, report
    ld s6, NONCE(s1)
    ld s7, NONCE+8(s1)
    ld s8, NONCE+16(s1)
    ld s9, NONCE+24(s1)
    bne s6, s2, check_malformed
    bne s7, s3, check_malformed
    bne s8, s4, check_malformed
    beq s9, s5, report

check_malformed:
    // Every site responds at its own aperture and publishes its own domain.
    li a0, 10
    li t1, READY
    srli t2, s5, 32
    li t3, ROCKET_SITE_COUNT
    li a1, 0
    mv a2, s0
1:  lw t0, STATUS(a2)
    bne t0, t1, report
    lwu t0, NONCE+28(a2)
    add t4, t2, a1
    bne t0, t4, report
    addi a1, a1, 1
    addi a2, a2, SITE_STRIDE
    blt a1, t3, 1b

    li a0, 3
    la a1, bad_signature
    jal ra, submit_signature
    jal ra, await_result
    li t1, READY+DONE
    bne t0, t1, report
    jal ra, check_initial_nonce

    li a0, 4
    la a1, signature_zero
    jal ra, submit_signature
    lw t0, STATUS(s0)
    li t1, BUSY
    bne t0, t1, report

    li a0, 5
    li t0, 0x80001000
    li t1, 17
    li t2, 44
    add t1, t1, t2
    sd t1, 0(t0)
    fence rw, rw
    ld t2, 0(t0)
    li t3, 61
    bne t2, t3, report

#ifndef ROCKET_BOOT_CHECK
    li a0, 6
    jal ra, await_result
    li t1, READY+SIGNER_ONE+DONE+PASSED
    bne t0, t1, report
    jal ra, check_initial_nonce

    li a0, 7
    la a1, signature_one
    jal ra, submit_signature
    jal ra, await_result
    li t1, READY+ENABLED+DONE+PASSED
    bne t0, t1, report

    li a0, 8
    ld t0, NONCE(s0)
    bne t0, s2, check_other_site
    ld t0, NONCE+8(s0)
    bne t0, s3, check_other_site
    ld t0, NONCE+16(s0)
    bne t0, s4, check_other_site
    ld t0, NONCE+24(s0)
    beq t0, s5, report

#endif

check_other_site:
    li a0, 9
    lw t0, STATUS(s1)
    li t1, READY
    bne t0, t1, report
    ld t0, NONCE(s1)
    bne t0, s6, report
    ld t0, NONCE+8(s1)
    bne t0, s7, report
    ld t0, NONCE+16(s1)
    bne t0, s8, report
    ld t0, NONCE+24(s1)
    bne t0, s9, report
    li a0, 0

report:
    li t0, 0x60000000
    sw a0, 0(t0)
1:  j 1b

    .balign 4
trap:
    csrr a0, mcause
    ori a0, a0, 256
    j report

submit_signature:
    addi t0, s0, SIGNATURE
    addi t1, t0, 64
1:  ld t2, 0(a1)
    sd t2, 0(t0)
    addi t0, t0, 8
    addi a1, a1, 8
    bne t0, t1, 1b
    fence iorw, iorw
    li t0, 1
    sw t0, SUBMIT(s0)
    fence iorw, iorw
    ret

await_result:
    lw t0, STATUS(s0)
    andi t1, t0, READY+DONE
    li t2, READY+DONE
    bne t1, t2, await_result
    ret

check_initial_nonce:
    ld t0, NONCE(s0)
    bne t0, s2, report
    ld t0, NONCE+8(s0)
    bne t0, s3, report
    ld t0, NONCE+16(s0)
    bne t0, s4, report
    ld t0, NONCE+24(s0)
    bne t0, s5, report
    ret

    .balign 8
bad_signature:
    .zero 64
#include "off_switch_rocket_vectors.inc"
