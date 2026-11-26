"""Read a pipeline temperature and print NORMAL (below 80), WARNING (80-99) or DANGER (100 or above)."""


def classify(temperature):
    if temperature < 80:
        return "NORMAL"
    if temperature < 100:
        return "WARNING"
    return "DANGER"


if __name__ == "__main__":
    print(classify(float(input("Enter pipeline temperature (C): "))))
