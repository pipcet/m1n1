/* SPDX-License-Identifier: MIT */

#include "payload.h"
#include "assert.h"
#include "heapblock.h"
#include "kboot.h"
#include "smp.h"
#include "utils.h"

#include "malloc.h"
#include "libfdt/libfdt.h"
#include "minilzlib/minlzma.h"
#include "tinf/tinf.h"

// Kernels must be 2MB aligned
#define KERNEL_ALIGN (2 << 20)

const u8 gz_magic[] = {0x1f, 0x8b};
const u8 xz_magic[] = {0xfd, '7', 'z', 'X', 'Z', 0x00};
const u8 fdt_magic[] = {0xd0, 0x0d, 0xfe, 0xed};
const u8 kernel_magic[] = {'A', 'R', 'M', 0x64};   // at 0x38
const u8 cpio_magic[] = {'0', '7', '0', '7', '0'}; // '1' or '2' next
const u8 macho_magic[] = { 0xcf, 0xfa, 0xed, 0xfe };
const u8 empty[] = {0, 0, 0, 0};

struct kernel_header *kernel = NULL;
void *macho_start_pc = NULL;
void *macho_start_secondary_pc = NULL;
void *fdt = NULL;

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
    fdt = p;
    assert(!size || size == fdt_totalsize(fdt));
    return ((u8 *)p) + fdt_totalsize(fdt);
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

struct macho_command {
    u32 type;
    u32 size;
    union {
	struct {
	    u32 thread_type;
	    u32 length;
	    u64 regs[32];
	    u64 pc;
	    u64 regs2[1];
	} unix_thread;
	struct {
	    char segname[16];
	    u64 vmaddr;
	    u64 vmsize;
	    u64 fileoff;
	    u64 filesize;
	    u64 unused2[2];
	} segment_64;
    } u;
};

static void *load_macho(void *start, size_t size)
{
    UNUSED(size);
    struct macho_command *last_command = start + 32 + ((u32 *)start)[5];
    struct macho_command *command = start + 32;
    u64 pc = 0;
    u64 vmbase = 0;
    u64 vmtotalsize = 0;
    while (command < last_command) {
	switch (command->type) {
	case 0x05:
	    pc = command->u.unix_thread.pc;
	    break;
	case 0x19: {
	    u64 vmaddr = command->u.segment_64.vmaddr;
	    u64 vmsize = command->u.segment_64.vmsize;

	    if (vmbase == 0)
		vmbase = vmaddr;
	    if (vmsize + vmbase - vmaddr > vmtotalsize)
		vmtotalsize = vmsize + vmaddr - vmbase;
	    break;
	}
	}
	command = (void *)command + command->size;
    }
    void *dest = memalign(0x10000, vmtotalsize);
    memset(dest, 0, vmtotalsize);
    command = start + 32;
    void *virtpc = NULL;
    while (command < last_command) {
	switch (command->type) {
	case 0x19: {
	    if (vmbase == 0)
		vmbase = command->u.segment_64.vmaddr;
	    u64 vmaddr = command->u.segment_64.vmaddr;
	    u64 vmsize = command->u.segment_64.vmsize;
	    u64 pcoff = pc - vmaddr;
	    u64 fileoff = command->u.segment_64.fileoff;
	    u64 filesize = command->u.segment_64.filesize;

	    printf("pcoff %p vmaddr %p vmbase %p vmsize %p file %p %p\n", pcoff,
		   vmaddr, vmbase, vmsize, fileoff, filesize);
	    memcpy(dest + vmaddr - vmbase, start + fileoff, filesize);
	    if (pcoff < vmsize) {

		if (pcoff < filesize) {
		    virtpc = dest + vmaddr - vmbase + pcoff;
		    macho_start_secondary_pc = dest + vmaddr - vmbase;
		}
	    }
	}
	}
	command = (void *)command + command->size;
    }

    macho_start_pc = virtpc;
    printf("pcs %p %p\n", macho_start_pc, macho_start_secondary_pc);

    return NULL;
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
        printf("Found a devicetree at %p\n", p);
        return load_fdt(p, size);
    } else if (!memcmp(p, cpio_magic, sizeof cpio_magic)) {
        printf("Found a cpio initramfs at %p\n", p);
        return load_cpio(p, size);
    } else if (!memcmp(p + 0x38, kernel_magic, sizeof kernel_magic)) {
        printf("Found a kernel at %p\n", p);
        return load_kernel(p, size);
    } else if (!memcmp(p, macho_magic, sizeof macho_magic)) {
        printf("Found a Mach-O image at %p\n", p);
        return load_macho(p, size);
    } else if (!memcmp(p, empty, sizeof empty)) {
        printf("No more payloads at %p\n", p);
        return NULL;
    } else {
        printf("Unknown payload at %p (magic: %02x%02x%02x%02x)\n", p, p[0], p[1], p[2], p[3]);
        return NULL;
    }
}

extern u64 boot_args_addr;
extern void mmu_shutdown(void);

int macho_boot(void *entry, void *secondary_entry)
{
#if 0
    smp_start_secondaries();
    for (int i = 0; i < 8; i++)
	smp_call4(i, secondary_entry, 0, 0, 0, 0);
#endif
    mmu_shutdown();
    printf("calling macho at %p / %p\n",
	   entry, secondary_entry);

    ((void (*)(u64, u64, u64, u64))entry)(boot_args_addr, 0, 0, 0);

    panic("macho call returned\n");
}

int payload_run(void)
{
    void *p = _payload_start;

    while (p)
        p = load_one_payload(p, 0);

    if (macho_start_pc) {
	return macho_boot(macho_start_pc, macho_start_secondary_pc);
    }

    if (kernel && fdt) {
        smp_start_secondaries();

        if (kboot_prepare_dt(fdt)) {
            printf("Failed to prepare FDT!");
            return -1;
        }

        return kboot_boot(kernel);
    }

    return -1;
}
