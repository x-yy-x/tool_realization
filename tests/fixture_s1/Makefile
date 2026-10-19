.PHONY: all clean
all:
	@timeout 500 hb build
	$(CC) -o image $(OBJS) -lc -lnosys -L /sysroot/lib
clean:
	-rm -f image
