# R cross-check for the Estimate Sample Size tool
# (Congalton-Green / Tortora 1978 multinomial + Olofsson et al. 2014 eq. 13)

# ---- shared pieces --------------------------------------------------------
k    <- 4          # number of map classes
conf <- 0.95       # confidence level
b    <- 0.10       # desired precision (as a proportion; 10% -> 0.10)
# Bonferroni share of alpha per class (Tortora 1978):
B <- qchisq(1 - (1 - conf) / k, df = 1)
fpc <- function(n, N) n / (1 + (n - 1) / N)   # finite population correction

# ---- Method 1: worst case (a class at exactly 50%) ------------------------
n1 <- ceiling(B * 0.5 * 0.5 / b^2)
cat("Worst case:            n =", n1, "\n")                       # 156
cat("  FPC, N = 10,000:     n =", ceiling(fpc(B*0.25/b^2, 1e4)), "\n")  # 154

# ---- Method 2: largest class proportion (always the one closest to 50%) ---
p  <- 0.4299035484                            # oak juniper, from the raster
n2 <- ceiling(B * p * (1 - p) / b^2)
cat("Largest proportion:    n =", n2, "\n")                       # 153

# ---- Method 3: per-class proportions and precisions (binding class governs)
props <- c(0.3572267502, 0.0813254084, 0.4299035484, 0.1315442930)
precs <- c(0.05, 0.10, 0.10, 0.15)
n3 <- ceiling(max(B * props * (1 - props) / precs^2))
cat("Per-class precisions:  n =", n3, "\n")

# ---- Method 4: target SE of overall accuracy (Olofsson et al. 2014 eq. 13)
W  <- props                                   # map proportions (strata weights)
U  <- c(0.05, 0.10, 0.10, 0.15)               # conjectured user's accuracies
S  <- sqrt(U * (1 - U))                       # per-stratum standard deviations
target_se <- 0.01                             # 1% -> 0.01 as a proportion
n4 <- ceiling((sum(W * S) / target_se)^2)
cat("Olofsson target SE:    n =", n4, "\n")                       # 774

# the manual's Option 3 example (Observer 2 sample margins) -> 580
props_m <- c(0.367403, 0.151934, 0.135359, 0.345304)
cat("Manual Option 3 row:   n =",
    ceiling(max(B * props_m * (1 - props_m) / precs^2)),
    ceiling(fpc(max(B * props_m * (1 - props_m) / precs^2), 1e4)),
    ceiling(fpc(max(B * props_m * (1 - props_m) / precs^2), 1e3)), "\n")
