USE [CRE_Trial_ELT_Repository]
GO

/****** Object:  Table [dbo].[Data]    Script Date: 9/1/2026 12:00:41 PM ******/
SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

CREATE TABLE [dbo].[Data](
	[DataID] [int] IDENTITY(1,1) NOT NULL,
	[ClientID] [int] NULL,
	[TreatyIncept] [date] NULL,
	[DataVintage] [date] NULL,
	[DataName] [nvarchar](150) NULL,
	[DataModelVendor] [nvarchar](10) NULL,
	[DataModelVersion] [nvarchar](10) NULL,
	[DataCurrency] [nvarchar](5) NULL,
	[Server] [varchar](max) NULL,
	[Database] [varchar](max) NULL,
	[AnalysisID] [int] NULL,
	[Name] [varchar](max) NULL,
	[Description] [varchar](max) NULL,
	[Perspective] [varchar](50) NULL,
	[ArchiveFile] [varchar](100) NULL,
	[AReLossSet] [nvarchar](36) NULL,
	[LOB] [nvarchar](500) NULL,
	[Geography] [nvarchar](500) NULL,
	[CRMID] [varchar](30) NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO


