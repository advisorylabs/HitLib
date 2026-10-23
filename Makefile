################################################################################
######################### User configurable parameters #########################
# filename extensions
CEXTS:=c
ASMEXTS:=s S
CXXEXTS:=cpp c++ cc

# probably shouldn't modify these, but you may need them below
ROOT=.
FWDIR:=$(ROOT)/firmware
BINDIR=$(ROOT)/bin
SRCDIR=$(ROOT)/src
INCDIR=$(ROOT)/include

WARNFLAGS+=
EXTRA_CFLAGS=
EXTRA_CXXFLAGS=

# Set to 1 to enable hot/cold linking
USE_PACKAGE:=1

# Add libraries you do not wish to include in the cold image here
# EXCLUDE_COLD_LIBRARIES:= $(FWDIR)/your_library.a
EXCLUDE_COLD_LIBRARIES:=

# Set this to 1 to add additional rules to compile your project as a PROS library template
IS_LIBRARY:=1
LIBNAME:=hitlib
VERSION:=1.5.1

# Exclude any top-level entry point files from the library archive
EXCLUDE_SRC_FROM_LIB+=$(foreach file, $(SRCDIR)/main $(SRCDIR)/autonomous $(SRCDIR)/opcontrol $(SRCDIR)/initialize,\
	$(foreach cext,$(CEXTS),$(file).$(cext))\
	$(foreach cxxext,$(CXXEXTS),$(file).$(cxxext)))

# Header files distributed to every user.
# Includes all .h and .hpp files anywhere under include/hitlib/ (recursive).
TEMPLATE_FILES=$(shell find $(INCDIR)/$(LIBNAME) -name '*.h' -o -name '*.hpp')

# Kernels before 4.2.0 compile with -mfloat-abi=softfp, and the linker won't
# mix that with the hard-float archive this project builds. `make softfp`
# builds a second archive for kernel 4.1.x projects, in bin/softfp/, against
# that kernel's own headers: point SOFTFP_KERNEL at an unzipped kernel@4.1.x
# template. Its include/ is searched ahead of this project's, so its pros/
# headers replace the 4.2 ones here. SOFTFP_MFLAGS is that kernel's MFLAGS;
# the library's other compile flags are the same in both kernels.
SOFTFP_KERNEL?=
SOFTFP_MFLAGS:=-mcpu=cortex-a9 -mfpu=neon-fp16 -mfloat-abi=softfp -Os -g

.PHONY: softfp
softfp:
	$(if $(wildcard $(SOFTFP_KERNEL)/include/pros/adi.hpp),,$(error Set SOFTFP_KERNEL to an unzipped kernel@4.1.x template))
	$(MAKE) library BINDIR=$(BINDIR)/softfp DEPDIR=.d/softfp MFLAGS="$(SOFTFP_MFLAGS)" \
		INCLUDE='-iquote"$(SOFTFP_KERNEL)/include" -iquote"$(INCDIR)"'

.DEFAULT_GOAL=quick

################################################################################
################################################################################
########## Nothing below this line should be edited by typical users ###########
-include ./common.mk