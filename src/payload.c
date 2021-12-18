/* SPDX-License-Identifier: MIT */

#include "payload.h"
#include "adt.h"
#include "assert.h"
#include "heapblock.h"
#include "kboot.h"
#include "smp.h"
#include "utils.h"

#include "libfdt/libfdt.h"
#include "minilzlib/minlzma.h"
#include "tinf/tinf.h"

// Kernels must be 2MB aligned
#define KERNEL_ALIGN (2 << 20)

static const u8 gz_magic[] = {0x1f, 0x8b};
static const u8 xz_magic[] = {0xfd, '7', 'z', 'X', 'Z', 0x00};
static const u8 fdt_magic[] = {0xd0, 0x0d, 0xfe, 0xed};
static const u8 kernel_magic[] = {'A', 'R', 'M', 0x64};   // at 0x38
static const u8 cpio_magic[] = {'0', '7', '0', '7', '0'}; // '1' or '2' next
static const u8 empty[] = {0, 0, 0, 0};

static char expect_compatible[256];
static struct kernel_header *kernel = NULL;
static void *fdt = NULL;

static void *load_one_payload(void *start, size_t size);

static void finalize_uncompression(void *dest, size_t dest_len)
{
    // Actually reserve the space. malloc is safe after this, but...
    assert(dest == heapblock_alloc_aligned(dest_len, KERNEL_ALIGN));

    void *end = ((u8 *)dest) + dest_len;
    void *next = load_one_payload(dest, dest_len);
    assert(!next || next >= dest);

    // If the payload needs padding, we need to reserve more, so it better have not used
    // malloc either.
    if (next > end) {
        // Explicitly *un*aligned or it'll fail this assert, since 64b alignment is the default
        assert(end == heapblock_alloc_aligned((u8 *)next - (u8 *)end, 1));
    }
}

static void *decompress_gz(void *p, size_t size)
{
    unsigned int source_len = size, dest_len = 1 << 30; // 1 GiB should be enough hopefully

    // Start at the end of the heap area, no allocation yet. The following code must not use
    // malloc or heapblock, until finalize_uncompression is called.
    void *dest = heapblock_alloc_aligned(0, KERNEL_ALIGN);

    printf("Uncompressing... ");
    int ret = tinf_gzip_uncompress(dest, &dest_len, p, &source_len);

    if (ret != TINF_OK) {
        printf("Error %d\n", ret);
        return NULL;
    }

    printf("%d bytes uncompressed to %d bytes\n", source_len, dest_len);

    finalize_uncompression(dest, dest_len);

    return ((u8 *)p) + source_len;
}

static void *decompress_xz(void *p, size_t size)
{
    uint32_t source_len = size, dest_len = 1 << 30; // 1 GiB should be enough hopefully

    // Start at the end of the heap area, no allocation yet. The following code must not use
    // malloc or heapblock, until finalize_uncompression is called.
    void *dest = heapblock_alloc_aligned(0, KERNEL_ALIGN);

    printf("Uncompressing... ");
    int ret = XzDecode(p, &source_len, dest, &dest_len);

    if (!ret) {
        printf("XZ decode failed\n");
        return NULL;
    }

    printf("%d bytes uncompressed to %d bytes\n", source_len, dest_len);

    finalize_uncompression(dest, dest_len);

    return ((u8 *)p) + source_len;
}

static void *load_fdt(void *p, size_t size)
{
    if (fdt_node_check_compatible(p, 0, expect_compatible) == 0) {
        printf("Found a devicetree for %s at %p\n", expect_compatible, p);
        fdt = p;
    }
    assert(!size || size == fdt_totalsize(p));
    return ((u8 *)p) + fdt_totalsize(p);
}

static void *load_cpio(void *p, size_t size)
{
    if (!size) {
        // We could handle this, but who uses uncompressed initramfs?
        printf("Uncompressed cpio archives not supported\n");
        return NULL;
    }

    kboot_set_initrd(p, size);
    return ((u8 *)p) + size;
}

static void *load_kernel(void *p, size_t size)
{
    kernel = p;

    assert(size <= kernel->image_size);

    // If this is an in-line kernel, it's probably not aligned, so we need to make a copy
    if (((u64)kernel) & (KERNEL_ALIGN - 1)) {
        void *new_addr = heapblock_alloc_aligned(kernel->image_size, KERNEL_ALIGN);
        memcpy(new_addr, kernel, size ? size : kernel->image_size);
        kernel = new_addr;
    }

    /*
     * Kernel blobs unfortunately do not have an accurate file size header, so
     * this will fail for in-line payloads. However, conversely, this is required for
     * compressed payloads, in order to allocate padding that the kernel needs, which will be
     * beyond the end of the compressed data. So if we know the input size, tell the caller
     * about the true image size; otherwise don't.
     */
    if (size) {
        return ((u8 *)p) + kernel->image_size;
    } else {
        return NULL;
    }
}

#define MAX_VAR_NAME 32
#define MAX_VAR_SIZE 1024

#define IS_VAR(x) !strncmp((char *)*p, x, strlen(x))

static bool check_var(u8 **p)
{
    char *val = memchr(*p, '=', MAX_VAR_NAME + 1);
    if (!val)
        return false;

    val++;

    char *end = memchr(val, '\n', MAX_VAR_SIZE + 1);
    if (!end)
        return false;

    if (IS_VAR("boot-args=")) {
        *end = 0;
        kboot_set_bootargs(val);
    } else {
        return false;
    }

    printf("Found a variable at %p: %s\n", *p, (char *)*p);
    *p = (u8 *)(end + 1);
    return true;
}

static void *load_one_payload(void *start, size_t size)
{
    u8 *p = start;

    if (!start)
        return NULL;

    if (!memcmp(p, gz_magic, sizeof gz_magic)) {
        printf("Found a gzip compressed payload at %p\n", p);
        return decompress_gz(p, size);
    } else if (!memcmp(p, xz_magic, sizeof xz_magic)) {
        printf("Found an XZ compressed payload at %p\n", p);
        return decompress_xz(p, size);
    } else if (!memcmp(p, fdt_magic, sizeof fdt_magic)) {
        return load_fdt(p, size);
    } else if (!memcmp(p, cpio_magic, sizeof cpio_magic)) {
        printf("Found a cpio initramfs at %p\n", p);
        return load_cpio(p, size);
    } else if (!memcmp(p + 0x38, kernel_magic, sizeof kernel_magic)) {
        printf("Found a kernel at %p\n", p);
        return load_kernel(p, size);
    } else if (check_var(&p)) {
        return p;
    } else if (!memcmp(p, empty, sizeof empty)) {
        printf("No more payloads at %p\n", p);
        return NULL;
    } else {
        printf("Unknown payload at %p (magic: %02x%02x%02x%02x)\n", p, p[0], p[1], p[2], p[3]);
        return NULL;
    }
}

int payload_run(void)
{
    const char *target = adt_getprop(adt, 0, "target-type", NULL);
    if (target) {
        strcpy(expect_compatible, "apple,");
        char *p = expect_compatible + strlen(expect_compatible);
        while (*target && p != expect_compatible + sizeof(expect_compatible) - 1) {
            *p++ = tolower(*target++);
        }
        *p = 0;
        printf("Devicetree compatible value: %s\n", expect_compatible);
    } else {
        printf("Cannot find target type! %p %p\n", target, adt);
        return -1;
    }

    void *p = _payload_start;

    while (p)
        p = load_one_payload(p, 0);

    if (kernel && fdt) {
        smp_start_secondaries();

        if (kboot_prepare_dt(fdt)) {
            printf("Failed to prepare FDT!\n");
            return -1;
        }

        return kboot_boot(kernel);
    } else if (kernel && !fdt) {
        printf("ERROR: Kernel found but no devicetree for %s available.\n", expect_compatible);
    } else if (!kernel && fdt) {
        printf("ERROR: Devicetree found but no kernel.\n");
    }

    return -1;
}
