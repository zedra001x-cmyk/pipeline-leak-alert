"""Read 10 tank levels (percent) and print the average."""

NUM_TANKS = 10


def read_level(n):
    while True:
        try:
            level = float(input(f"Enter level of tank {n} (0-100 %): "))
        except ValueError:
            print("Please type a number.")
            continue
        if 0 <= level <= 100:
            return level
        print("The level must be between 0 and 100.")


def main():
    total = 0.0
    for n in range(1, NUM_TANKS + 1):
        total += read_level(n)
    print(f"Average tank level: {total / NUM_TANKS:.1f}%")


if __name__ == "__main__":
    main()
