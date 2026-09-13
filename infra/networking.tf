# Minimal networking for RDS (public subnet) and ECS Fargate. No NAT
# gateway (~$32/month) -- ECS tasks get a public IP directly instead,
# same public-subnet-only design as RDS. This is the single largest
# deliberate cost decision in the project (BUILD-GUIDE.md step 7.2), not
# an oversight: a batch job and a small API don't need outbound access
# routed through a NAT, and this design doesn't give them any egress path
# that a NAT gateway would have anyway (both patterns expose the workload
# directly to the internet on an ENI, gated by security groups either way).

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project_name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project_name}-public-a" }
}

resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.2.0/24"
  availability_zone       = "${var.aws_region}b"
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project_name}-public-b" }
  # RDS requires a subnet group spanning >= 2 AZs even for a single-AZ
  # instance -- this subnet exists for that requirement, not for HA.
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "public_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "public_b" {
  subnet_id      = aws_subnet.public_b.id
  route_table_id = aws_route_table.public.id
}

# --- RDS ---

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnets"
  subnet_ids = [aws_subnet.public_a.id, aws_subnet.public_b.id]
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg"
  description = "Locked down: Postgres only, only from the ECS security group."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Postgres from the ECS service/task only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "churn" {
  identifier              = "${var.project_name}-db"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = var.db_instance_class
  allocated_storage       = 20
  storage_type            = "gp3"
  db_name                 = "churn"
  username                = "churn"
  password                = var.db_password
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  publicly_accessible     = true # public subnet per guide — locked down by the security group above, not by network placement
  skip_final_snapshot     = true # student-budget project; document this tradeoff, don't hide it
  backup_retention_period = 0
  deletion_protection     = false
  apply_immediately       = true
}
