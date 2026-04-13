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
VERSION:=0.7.6

# Exclude any top-level entry point files from the library archive
EXCLUDE_SRC_FROM_LIB+=$(foreach file, $(SRCDIR)/main $(SRCDIR)/autonomous $(SRCDIR)/opcontrol $(SRCDIR)/initialize,\
	$(foreach cext,$(CEXTS),$(file).$(cext))\
	$(foreach cxxext,$(CXXEXTS),$(file).$(cxxext)))

# Header files distributed to every user.
# Includes all .h and .hpp files anywhere under include/hitlib/ (recursive).
TEMPLATE_FILES=$(shell find $(INCDIR)/$(LIBNAME) -name '*.h' -o -name '*.hpp')

.DEFAULT_GOAL=quick

################################################################################
################################################################################
########## Nothing below this line should be edited by typical users ###########
-include ./common.mk