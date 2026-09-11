USE [CRE_Cat_Workflow]
GO
/****** Object:  Table [dbo].[Lookup_RMS_HistoricalRDS]    Script Date: 9/1/2026 11:49:12 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[Lookup_RMS_HistoricalRDS](
	[EventID] [int] NULL,
	[CatYear] [int] NULL,
	[Peril] [nvarchar](10) NULL,
	[Type] [nvarchar](10) NULL,
	[Name] [nvarchar](max) NULL,
	[PCS#] [nvarchar](225) NULL,
	[ModelVersion] [nvarchar](10) NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO
